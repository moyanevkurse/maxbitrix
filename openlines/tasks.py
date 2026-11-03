from __future__ import annotations

import logging
import random
import time
from contextlib import contextmanager
from pathlib import Path

import requests
from celery import shared_task
from django.conf import settings
from django.db import transaction

from .entity_phones import resolve_entity_phones
from .services_max_groups import MaxGroupCreateError, create_max_group_via_ws, refresh_max_ws_token

logger = logging.getLogger(__name__)


@contextmanager
def _file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock_file:
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:  # noqa: BLE001
                pass


def _looks_like_max_auth_error(error_text: str) -> bool:
    """
    Грубая эвристика: стоит ли пробовать refresh токена и один ретрай создания группы.
    """
    text = str(error_text or "").lower()
    if not text:
        return False
    hints = (
        "opcode=19",
        "unauthorized",
        "auth",
        "token",
        "forbidden",
        "session",
    )
    return any(hint in text for hint in hints)


def _check_and_mark_task_interval(*, lock_path: Path, state_path: Path, min_interval_sec: int) -> tuple[bool, int]:
    """
    Глобальный throttle для create_max_group_from_webhook между воркерами.
    Возвращает (allowed, retry_after_sec).
    """
    interval_sec = max(int(min_interval_sec or 0), 0)
    if interval_sec <= 0:
        return True, 0

    now_ts = time.time()
    with _file_lock(lock_path):
        last_ts = 0.0
        if state_path.exists():
            try:
                last_ts = float((state_path.read_text(encoding="utf-8") or "0").strip() or "0")
            except Exception:  # noqa: BLE001
                last_ts = 0.0

        elapsed = now_ts - last_ts
        if last_ts > 0 and elapsed < interval_sec:
            retry_after = max(int(interval_sec - elapsed), 1)
            return False, retry_after

        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(f"{now_ts:.6f}", encoding="utf-8")
        return True, 0


@shared_task(bind=True)
def send_welcome_to_max_group(
    self,
    *,
    chat_id: str,
    title: str = "",
):
    """
    Приветствие в новую MAX-группу: текст (+ invite-ссылка) и вложения из MEDIA_ROOT.
    """
    from mailings.services.max import MaxApiError, send_media_message, send_message as max_send_message

    from .max_group_welcome import (
        build_welcome_message_text,
        fetch_group_invite_link,
        get_max_group_welcome_text,
        normalize_max_chat_id,
        resolve_welcome_media_files,
    )

    welcome_text = str(get_max_group_welcome_text() or '').strip()
    if not welcome_text:
        return {"ok": True, "skipped": True, "reason": "welcome text is empty"}

    normalized_chat_id = normalize_max_chat_id(chat_id)
    if not normalized_chat_id:
        return {"ok": False, "error": "chat_id is empty"}

    invite_link = ""
    if bool(getattr(settings, "MAX_GROUP_WELCOME_INCLUDE_INVITE_LINK", True)):
        invite_link = fetch_group_invite_link(normalized_chat_id)

    text = build_welcome_message_text(
        welcome_text=welcome_text,
        title=title,
        chat_id=normalized_chat_id,
        invite_link=invite_link,
    )

    try:
        result = max_send_message(normalized_chat_id, text)
    except MaxApiError as exc:
        logger.error(
            "MAX welcome send failed: task_id=%s chat_id=%s error=%s",
            self.request.id,
            normalized_chat_id,
            exc,
        )
        return {"ok": False, "error": str(exc), "chat_id": normalized_chat_id}

    if not result.get("ok"):
        error = result.get("error") or "unknown MAX error"
        logger.warning(
            "MAX welcome send returned error: task_id=%s chat_id=%s error=%s",
            self.request.id,
            normalized_chat_id,
            error,
        )
        return {"ok": False, "error": error, "chat_id": normalized_chat_id}

    media_files = resolve_welcome_media_files()
    media_delay = max(float(getattr(settings, "MAX_GROUP_WELCOME_MEDIA_DELAY_SEC", 1.5) or 0), 0)
    media_sent = 0
    media_errors: list[str] = []

    for index, (file_path, send_type) in enumerate(media_files):
        if index > 0 and media_delay > 0:
            time.sleep(media_delay)
        try:
            media_result = send_media_message(
                normalized_chat_id,
                str(file_path),
                send_type=send_type,
                text='',
            )
            if media_result.get("ok"):
                media_sent += 1
                logger.info(
                    "MAX welcome media sent: chat_id=%s file=%s type=%s",
                    normalized_chat_id,
                    file_path.name,
                    send_type,
                )
            else:
                err = str(media_result.get("error") or "unknown media error")
                media_errors.append(f'{file_path.name}: {err}')
        except MaxApiError as exc:
            media_errors.append(f'{file_path.name}: {exc}')
            logger.warning(
                "MAX welcome media failed: chat_id=%s file=%s error=%s",
                normalized_chat_id,
                file_path.name,
                exc,
            )

    logger.info(
        "MAX welcome sent: task_id=%s chat_id=%s title=%s invite=%s media=%s/%s",
        self.request.id,
        normalized_chat_id,
        title,
        bool(invite_link),
        media_sent,
        len(media_files),
    )
    return {
        "ok": True,
        "chat_id": normalized_chat_id,
        "invite_link": invite_link,
        "media_sent": media_sent,
        "media_total": len(media_files),
        "media_errors": media_errors,
    }


@shared_task(bind=True)
def sync_max_group_from_bot_event(
    self,
    *,
    chat_id: str,
    title: str = '',
    update_type: str = '',
):
    """
    Связка MAX-группы с контактом и/или компанией Bitrix по номеру из названия чата
    (bot_added / chat_title_changed).
    """
    from mailings.services.max import get_chat_title

    from .max_group_bitrix_sync import sync_max_group_to_bitrix_contact

    normalized_chat_id = str(chat_id or '').strip()
    if not normalized_chat_id:
        return {'ok': False, 'error': 'chat_id is empty'}

    delay_sec = max(int(getattr(settings, 'MAX_GROUP_BITRIX_SYNC_TITLE_DELAY_SEC', 3) or 0), 0)
    if delay_sec > 0:
        logger.info(
            'MAX group bitrix sync delayed: chat_id=%s delay=%ss update_type=%s',
            normalized_chat_id,
            delay_sec,
            update_type,
        )
        time.sleep(delay_sec)

    resolved_title = str(title or '').strip()
    if not resolved_title:
        try:
            resolved_title = get_chat_title(normalized_chat_id) or ''
        except Exception as exc:  # noqa: BLE001
            logger.warning('MAX group bitrix sync: get_chat_title failed chat_id=%s: %s', normalized_chat_id, exc)

    logger.info(
        'MAX group bitrix sync started: task_id=%s chat_id=%s title=%s update_type=%s',
        self.request.id,
        normalized_chat_id,
        resolved_title[:200],
        update_type,
    )

    try:
        result = sync_max_group_to_bitrix_contact(
            chat_id=normalized_chat_id,
            title=resolved_title,
            update_type=str(update_type or ''),
        )
    except requests.exceptions.RequestException as exc:
        logger.error(
            'MAX group bitrix sync network error: chat_id=%s error=%s',
            normalized_chat_id,
            exc,
        )
        return {'ok': False, 'error': f'network error: {exc}', 'chat_id': normalized_chat_id}

    if result.get('ok'):
        try:
            from mailings.models import Partner

            partner_chat_id = f'max:{normalized_chat_id}'
            Partner.objects.update_or_create(
                chat_id=partner_chat_id,
                defaults={
                    'chat_type': Partner.TYPE_GROUP,
                    'title': (resolved_title or partner_chat_id)[:255],
                    'is_active': True,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug('MAX group bitrix sync: partner ensure failed: %s', exc)

    return result


@shared_task(bind=True)
def create_max_group_from_webhook(
    self,
    *,
    title: str,
    user_ids: list[int],
    source: str = "bitrix_webhook",
    request_id: str = "",
    entity_type: str = "",
    entity_id: str = "",
    contact_email: str = "",
    avatar_url: str = "",
):
    normalized_entity_type = str(entity_type or "").strip().lower()
    profile_dir = str(getattr(settings, "MAX_GROUP_PROFILE_DIR", "profile/.max-manual-profile"))
    token_file = str(getattr(settings, "MAX_GROUP_TOKEN_FILE", "profile/max_ws_token.env"))
    account_name = "текущий аккаунт из .env"
    if normalized_entity_type in {"contact", "company"}:
        from .models import MaxGroupCreatorConfig

        creator_config = MaxGroupCreatorConfig.objects.filter(entity_type=normalized_entity_type).first()
        if creator_config:
            profile_dir = str(creator_config.profile_dir or profile_dir).strip()
            token_file = str(creator_config.token_file or token_file).strip()
            account_name = creator_config.account_name or creator_config.get_entity_type_display()
    refresh_timeout = int(getattr(settings, "MAX_GROUP_REFRESH_TIMEOUT", 45) or 45)
    refresh_each_task = bool(getattr(settings, "MAX_GROUP_REFRESH_EACH_TASK", False))
    refresh_on_auth_fail = bool(getattr(settings, "MAX_GROUP_REFRESH_ON_AUTH_FAIL", True))
    bot_user_id = int(getattr(settings, "MAX_GROUP_BOT_USER_ID", 0) or 0)
    bot_grant_admin = bool(getattr(settings, "MAX_GROUP_BOT_GRANT_ADMIN", True))
    lock_path = Path(settings.BASE_DIR) / "profile" / ".max_group_refresh.lock"
    task_min_interval_sec = int(getattr(settings, "MAX_GROUP_TASK_MIN_INTERVAL_SEC", 60) or 60)
    task_interval_lock_path = Path(settings.BASE_DIR) / "profile" / ".max_group_task_interval.lock"
    task_interval_state_path = Path(settings.BASE_DIR) / "profile" / ".max_group_task_interval.state"

    logger.info(
        "MAX group task started: task_id=%s source=%s request_id=%s title=%s users=%s entity=%s:%s creator=%s",
        self.request.id,
        source,
        request_id,
        title,
        user_ids,
        entity_type,
        entity_id,
        account_name,
    )

    interval_ok, retry_after_sec = _check_and_mark_task_interval(
        lock_path=task_interval_lock_path,
        state_path=task_interval_state_path,
        min_interval_sec=task_min_interval_sec,
    )
    if not interval_ok:
        logger.warning(
            "MAX group task rate-limited: task_id=%s retry_after=%ss request_id=%s entity=%s:%s",
            self.request.id,
            retry_after_sec,
            request_id,
            entity_type,
            entity_id,
        )
        return {
            "ok": False,
            "stage": "task_rate_limit",
            "retry_after_sec": retry_after_sec,
            "error": f"create_max_group_from_webhook can run only once per {task_min_interval_sec} seconds",
        }

    if refresh_each_task:
        with _file_lock(lock_path):
            refresh_result = refresh_max_ws_token(
                profile_dir=profile_dir,
                token_file=token_file,
                timeout_sec=refresh_timeout,
            )
            if refresh_result.returncode != 0:
                message = (refresh_result.stderr or refresh_result.stdout or "").strip()
                logger.error("MAX token refresh failed: %s", message)
                return {
                    "ok": False,
                    "stage": "refresh_token",
                    "error": message or "token refresh failed",
                }

    try:
        create_result = create_max_group_via_ws(
            title=title,
            # Участников-клиентов добавляем отдельным REST вызовом после создания чата.
            # Это стабильнее, чем пытаться добавлять через reverse-engineered WS flow.
            user_ids=[],
            token_file=token_file,
            bot_user_id=bot_user_id,
            bot_grant_admin=bot_grant_admin,
        )
    except MaxGroupCreateError as exc:
        error_text = str(exc)
        if not (refresh_on_auth_fail and _looks_like_max_auth_error(error_text)):
            logger.error("MAX group create failed: %s", exc)
            return {
                "ok": False,
                "stage": "create_group",
                "error": error_text,
            }

        logger.warning("MAX group create auth-like failure, trying token refresh + one retry: %s", error_text)
        with _file_lock(lock_path):
            refresh_result = refresh_max_ws_token(
                profile_dir=profile_dir,
                token_file=token_file,
                timeout_sec=refresh_timeout,
            )
            if refresh_result.returncode != 0:
                message = (refresh_result.stderr or refresh_result.stdout or "").strip()
                logger.error("MAX token refresh after auth-like failure failed: %s", message)
                return {
                    "ok": False,
                    "stage": "refresh_token_after_auth_fail",
                    "error": message or "token refresh failed after auth-like failure",
                }

        try:
            create_result = create_max_group_via_ws(
                title=title,
                user_ids=user_ids,
                token_file=token_file,
                bot_user_id=bot_user_id,
                bot_grant_admin=bot_grant_admin,
            )
        except MaxGroupCreateError as retry_exc:
            logger.error("MAX group create failed after refresh retry: %s", retry_exc)
            return {
                "ok": False,
                "stage": "create_group_retry",
                "error": str(retry_exc),
            }

    logger.info(
        "MAX group task done: task_id=%s chat_id=%s title=%s",
        self.request.id,
        create_result.get("chat_id", ""),
        title,
    )
    created_chat_id = str(create_result.get("chat_id", "") or "").strip()
    user_ids_to_add: list[int] = []
    for uid in user_ids:
        try:
            uid_int = int(uid)
        except (TypeError, ValueError):
            continue
        if uid_int <= 0:
            continue
        if int(bot_user_id or 0) > 0 and uid_int == int(bot_user_id):
            # Бот добавляется отдельным шагом WS (и при необходимости админится),
            # не дублируем его в user add endpoint.
            continue
        if uid_int not in user_ids_to_add:
            user_ids_to_add.append(uid_int)

    if created_chat_id and user_ids_to_add:
        from mailings.services.max import MaxApiError, add_chat_members
        add_members_delay_sec = max(int(getattr(settings, "MAX_GROUP_ADD_MEMBERS_DELAY_SEC", 15) or 0), 0)

        try:
            if add_members_delay_sec > 0:
                logger.info(
                    "MAX group members add delayed: chat_id=%s delay=%ss",
                    created_chat_id,
                    add_members_delay_sec,
                )
                time.sleep(add_members_delay_sec)
            add_result = add_chat_members(created_chat_id, user_ids_to_add)
            logger.info(
                "MAX group members add result: chat_id=%s user_ids=%s result=%s",
                created_chat_id,
                user_ids_to_add,
                add_result,
            )
        except (MaxApiError, requests.exceptions.RequestException) as exc:
            logger.warning(
                "MAX group members add failed: chat_id=%s user_ids=%s error=%s",
                created_chat_id,
                user_ids_to_add,
                exc,
            )

    should_run_phone_fallback = (
        created_chat_id
        and bool(getattr(settings, "MAX_GROUP_PHONE_RESOLVE_ENABLED", True))
        and (
            not user_ids_to_add
            or normalized_entity_type in {"company", "contact"}
        )
    )
    if should_run_phone_fallback:
        from .services_max_groups import MaxPhoneResolveError, resolve_max_user_id_via_ws

        phone_resolve_timeout_sec = int(getattr(settings, "MAX_GROUP_PHONE_RESOLVE_TIMEOUT_SEC", 20) or 20)
        phone_resolve_delay_min_sec = max(int(getattr(settings, "MAX_GROUP_PHONE_RESOLVE_DELAY_MIN_SEC", 10) or 10), 0)
        phone_resolve_delay_max_sec = max(
            int(getattr(settings, "MAX_GROUP_PHONE_RESOLVE_DELAY_MAX_SEC", 20) or 20),
            phone_resolve_delay_min_sec,
        )
        resolved_phones = resolve_entity_phones(entity_type=entity_type, entity_id=entity_id)
        normalized_entity_id = str(entity_id or "").strip()
        if normalized_entity_type == "contact" and normalized_entity_id.isdigit():
            from .max_group_members import resolve_contact_group_extra_phones

            for phone in resolve_contact_group_extra_phones(normalized_entity_id):
                if phone not in resolved_phones:
                    resolved_phones.append(phone)
        if normalized_entity_type == "company" and normalized_entity_id.isdigit():
            from .max_group_members import resolve_company_group_extra_phones

            for phone in resolve_company_group_extra_phones(normalized_entity_id):
                if phone not in resolved_phones:
                    resolved_phones.append(phone)
        logger.info(
            "MAX group phone fallback: entity=%s:%s phones=%s",
            entity_type,
            entity_id,
            resolved_phones,
        )
        for phone in resolved_phones:
            delay_sec = random.randint(phone_resolve_delay_min_sec, phone_resolve_delay_max_sec)
            if delay_sec > 0:
                logger.info(
                    "MAX phone resolve delayed: phone=%s delay=%ss",
                    phone,
                    delay_sec,
                )
                time.sleep(delay_sec)
            try:
                resolved_user_id = int(
                    resolve_max_user_id_via_ws(
                        phone=phone,
                        token_file=token_file,
                        timeout_sec=phone_resolve_timeout_sec,
                    )
                )
            except MaxPhoneResolveError as exc:
                logger.warning("MAX phone resolve failed: phone=%s error=%s", phone, exc)
                continue
            if resolved_user_id <= 0:
                continue
            if int(bot_user_id or 0) > 0 and resolved_user_id == int(bot_user_id):
                continue
            if resolved_user_id not in user_ids_to_add:
                user_ids_to_add.append(resolved_user_id)

        if user_ids_to_add:
            from mailings.services.max import MaxApiError, add_chat_members

            add_members_delay_sec = max(int(getattr(settings, "MAX_GROUP_ADD_MEMBERS_DELAY_SEC", 15) or 0), 0)
            try:
                if add_members_delay_sec > 0:
                    logger.info(
                        "MAX group members add (phone fallback) delayed: chat_id=%s delay=%ss",
                        created_chat_id,
                        add_members_delay_sec,
                    )
                    time.sleep(add_members_delay_sec)
                add_result = add_chat_members(created_chat_id, user_ids_to_add)
                logger.info(
                    "MAX group members add via phone fallback: chat_id=%s user_ids=%s result=%s",
                    created_chat_id,
                    user_ids_to_add,
                    add_result,
                )
            except (MaxApiError, requests.exceptions.RequestException) as exc:
                logger.warning(
                    "MAX group members add via phone fallback failed: chat_id=%s user_ids=%s error=%s",
                    created_chat_id,
                    user_ids_to_add,
                    exc,
                )

    try:
        _bind_max_group_mapping(
            entity_type=entity_type,
            entity_id=entity_id,
            max_chat_id=create_result.get("chat_id", ""),
            title=title,
            contact_email=contact_email,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("MAX group mapping save failed: %s", exc, exc_info=True)

    avatar_url_effective = str(avatar_url or getattr(settings, "MAX_GROUP_AVATAR_URL", "") or "").strip()
    avatar_notify = bool(getattr(settings, "MAX_GROUP_AVATAR_NOTIFY", False))
    if created_chat_id and avatar_url_effective:
        from mailings.services.max import MaxApiError, set_chat_icon

        try:
            set_chat_icon(created_chat_id, avatar_url_effective, notify=avatar_notify)
            logger.info("MAX group avatar set: chat_id=%s url=%s", created_chat_id, avatar_url_effective)
        except MaxApiError as exc:
            logger.warning(
                "MAX group avatar set failed: chat_id=%s url=%s error=%s",
                created_chat_id,
                avatar_url_effective,
                exc,
            )

    from .max_group_welcome import get_max_group_welcome_text, welcome_will_include_invite_link

    invite_link_sent = ""
    if created_chat_id:
        skip_separate_invite = welcome_will_include_invite_link()
        if not skip_separate_invite and bool(getattr(settings, "MAX_GROUP_INVITE_LINK_ENABLED", True)):
            invite_link_sent = _send_max_group_invite_link(chat_id=created_chat_id, title=title)
        elif skip_separate_invite:
            logger.info(
                "MAX group invite link skipped (included in welcome): chat_id=%s",
                created_chat_id,
            )

    welcome_text = str(get_max_group_welcome_text() or '').strip()
    welcome_delay_sec = max(int(getattr(settings, "MAX_GROUP_WELCOME_DELAY_SEC", 60) or 60), 0)
    if created_chat_id and welcome_text:
        send_welcome_to_max_group.apply_async(
            kwargs={
                "chat_id": created_chat_id,
                "title": title,
            },
            countdown=welcome_delay_sec,
        )
        logger.info(
            "MAX welcome queued: chat_id=%s delay=%ss",
            created_chat_id,
            welcome_delay_sec,
        )

    return {
        "ok": True,
        "task_id": self.request.id,
        "chat_id": create_result.get("chat_id", ""),
        "invite_link": invite_link_sent,
        "source": source,
        "request_id": request_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
    }


def _send_max_group_invite_link(*, chat_id: str, title: str = "") -> str:
    """
    Запрашивает link у MAX API и отправляет его сообщением в созданную группу.
    Возвращает ссылку, если удалось отправить, иначе пустую строку.
    """
    if not bool(getattr(settings, "MAX_GROUP_INVITE_LINK_ENABLED", True)):
        return ""

    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        return ""

    delay_sec = max(int(getattr(settings, "MAX_GROUP_INVITE_LINK_DELAY_SEC", 5) or 0), 0)
    if delay_sec > 0:
        logger.info(
            "MAX group invite link delayed: chat_id=%s delay=%ss",
            normalized_chat_id,
            delay_sec,
        )
        time.sleep(delay_sec)

    from mailings.services.max import MaxApiError, get_chat_invite_link, send_message

    try:
        invite_link = get_chat_invite_link(normalized_chat_id)
    except MaxApiError as exc:
        logger.warning(
            "MAX group invite link fetch failed: chat_id=%s error=%s",
            normalized_chat_id,
            exc,
        )
        return ""

    if not invite_link:
        logger.info("MAX group invite link unavailable: chat_id=%s", normalized_chat_id)
        return ""

    template = str(getattr(settings, "MAX_GROUP_INVITE_LINK_TEXT", "") or "").strip()
    if not template:
        template = "Ссылка для входа в группу:\n{link}"
    message_text = (
        template.replace("{link}", invite_link)
        .replace("{chat_id}", normalized_chat_id)
        .replace("{title}", str(title or "").strip())
    )

    try:
        send_message(normalized_chat_id, message_text)
    except MaxApiError as exc:
        logger.warning(
            "MAX group invite link send failed: chat_id=%s link=%s error=%s",
            normalized_chat_id,
            invite_link,
            exc,
        )
        return ""

    logger.info(
        "MAX group invite link sent: chat_id=%s link=%s",
        normalized_chat_id,
        invite_link,
    )
    return invite_link


def _bind_max_group_mapping(
    *,
    entity_type: str,
    entity_id: str,
    max_chat_id: str,
    title: str,
    contact_email: str = "",
) -> None:
    """
    Сохраняет соответствие Bitrix сущности и созданной MAX-группы.
    Основной ключ — document_id (contact/company), fallback для контакта — email.
    """
    chat_id = str(max_chat_id or "").strip()
    if not chat_id:
        return
    partner_chat_id = f"max:{chat_id}" if not str(chat_id).startswith("max:") else str(chat_id)
    normalized_entity = str(entity_type or "").strip().lower()
    normalized_entity_id = str(entity_id or "").strip()
    normalized_email = str(contact_email or "").strip().lower()

    from mailings.models import BitrixCompanySnapshot, BitrixContactSnapshot, Partner

    with transaction.atomic():
        # MAX-группа должна быть доступна сразу в рассылках.
        Partner.objects.update_or_create(
            chat_id=partner_chat_id,
            defaults={
                "chat_type": Partner.TYPE_GROUP,
                "title": (title or partner_chat_id)[:255],
                "is_active": True,
            },
        )

        if normalized_entity == "contact" and normalized_entity_id.isdigit():
            contact, _ = BitrixContactSnapshot.objects.get_or_create(
                bitrix_contact_id=int(normalized_entity_id),
                defaults={
                    "full_name": "",
                },
            )
            contact.max_chat_id = partner_chat_id
            if normalized_email:
                contact.email = normalized_email
            contact.save(update_fields=["max_chat_id", "email", "synced_at"])
            return

        if normalized_entity == "company" and normalized_entity_id.isdigit():
            company, _ = BitrixCompanySnapshot.objects.get_or_create(
                bitrix_company_id=int(normalized_entity_id),
                defaults={
                    "title": "",
                },
            )
            company.max_chat_id = partner_chat_id
            parsed = dict(company.parsed_data or {})
            parsed["max_chat_id"] = partner_chat_id
            if normalized_email:
                parsed["contact_email"] = normalized_email
            company.parsed_data = parsed
            company.save(update_fields=["max_chat_id", "parsed_data", "synced_at"])
            return

        # Fallback, если робот не передал document_id, но передал email контакта.
        if normalized_email:
            updated = BitrixContactSnapshot.objects.filter(email__iexact=normalized_email).update(max_chat_id=partner_chat_id)
            if updated:
                logger.info("MAX group mapped by email fallback: email=%s updated=%s", normalized_email, updated)


@shared_task(bind=True)
def create_telegram_group_from_webhook(self, *, provision_id: int):
    """Создать Telegram-супергруппу по заявке Bitrix без браузера."""
    from .models import TelegramGroupProvision

    with transaction.atomic():
        provision = TelegramGroupProvision.objects.select_for_update().filter(pk=provision_id).first()
        if not provision:
            return {'ok': False, 'error': 'Telegram group provision not found'}
        if provision.status == TelegramGroupProvision.STATUS_SUCCESS:
            return {
                'ok': True,
                'skipped': True,
                'reason': 'already_created',
                'chat_id': provision.telegram_chat_id,
            }
        if provision.status == TelegramGroupProvision.STATUS_CREATING:
            return {'ok': False, 'skipped': True, 'reason': 'already_creating'}
        provision.status = TelegramGroupProvision.STATUS_CREATING
        provision.error_message = ''
        provision.save(update_fields=['status', 'error_message', 'updated_at'])

    try:
        from .services_telegram_groups import TelegramGroupCreateError, create_telegram_group
        from .telegram_group_bitrix_sync import bind_telegram_group_to_bitrix
        from .telegram_group_members import resolve_telegram_group_user_refs

        entity_refs = resolve_telegram_group_user_refs(provision.entity_type, provision.entity_id)
        requested_refs = list(provision.requested_user_refs or [])
        all_refs: list = []
        for reference in requested_refs + entity_refs:
            if reference not in all_refs:
                all_refs.append(reference)

        result = create_telegram_group(
            title=provision.title,
            member_refs=all_refs,
            bot_username=str(getattr(settings, 'TELEGRAM_GROUP_BOT_USERNAME', '') or ''),
            avatar_url=str(getattr(settings, 'TELEGRAM_GROUP_AVATAR_URL', '') or ''),
        )
        mapping = bind_telegram_group_to_bitrix(
            entity_type=provision.entity_type,
            entity_id=provision.entity_id,
            chat_id=result['chat_id'],
            title=provision.title,
        )
        result['audit'] = {
            'group': {
                'title': provision.title,
                'chat_id': str(result['chat_id']),
            },
            'participants': {
                'requested': all_refs,
                'added': result.get('invited') or [],
                'not_added': result.get('invite_errors') or [],
            },
            'bot_admin': result.get('bot_admin', ''),
        }
        result['bitrix_mapping'] = mapping

        with transaction.atomic():
            provision = TelegramGroupProvision.objects.select_for_update().get(pk=provision_id)
            provision.status = TelegramGroupProvision.STATUS_SUCCESS
            provision.telegram_chat_id = str(result['chat_id'])
            provision.result_data = result
            provision.error_message = ''
            provision.save(update_fields=['status', 'telegram_chat_id', 'result_data', 'error_message', 'updated_at'])

        from .telegram_group_welcome import welcome_content

        welcome_text, welcome_files = welcome_content()
        if welcome_text.strip() or welcome_files:
            send_welcome_to_telegram_group.apply_async(
                kwargs={'chat_id': str(result['chat_id']), 'title': provision.title},
                countdown=max(int(getattr(settings, 'TELEGRAM_GROUP_WELCOME_DELAY_SEC', 15) or 0), 0),
            )
        logger.info(
            'Telegram group audit: task_id=%s provision=%s title=%r chat_id=%s requested=%s added=%s '
            'not_added=%s bot_admin=%s',
            self.request.id,
            provision_id,
            provision.title,
            result['chat_id'],
            all_refs,
            result.get('invited') or [],
            result.get('invite_errors') or [],
            result.get('bot_admin', ''),
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception('Telegram group create failed: provision=%s error=%s', provision_id, exc)
        with transaction.atomic():
            provision = TelegramGroupProvision.objects.select_for_update().get(pk=provision_id)
            provision.status = TelegramGroupProvision.STATUS_ERROR
            provision.error_message = str(exc)[:4000]
            provision.save(update_fields=['status', 'error_message', 'updated_at'])
        return {'ok': False, 'error': str(exc), 'provision_id': provision_id}


@shared_task(bind=True)
def send_welcome_to_telegram_group(self, *, chat_id: str, title: str = ''):
    """Отправить приветствие и вложения уже добавленным в группу Telegram-ботом."""
    from .telegram_group_welcome import send_telegram_group_welcome

    try:
        return send_telegram_group_welcome(chat_id=str(chat_id), title=title)
    except Exception as exc:  # noqa: BLE001
        logger.exception('Telegram group welcome failed: task_id=%s chat_id=%s', self.request.id, chat_id)
        return {'ok': False, 'error': str(exc), 'chat_id': str(chat_id)}

