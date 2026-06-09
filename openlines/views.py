import json
import logging
import random
import re
import time
from pathlib import Path

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.decorators.clickjacking import xframe_options_exempt

from .services import BitrixAuthError, send_status_delivery, upsert_portal_from_auth_payload

logger = logging.getLogger(__name__)


@csrf_exempt
@xframe_options_exempt
@require_http_methods(['GET', 'POST'])
def bitrix_install(request: HttpRequest) -> HttpResponse:
    """
    Endpoint для первичной установки локального приложения Bitrix (доступен в iframe).
    """
    if request.method == 'POST' and request.POST:
        logger.info('Bitrix install callback keys: %s', list(request.POST.keys())[:30])
    html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Bitrix Install</title>
  <script src="//api.bitrix24.com/api/v1/"></script>
</head>
<body style="font-family:Arial,sans-serif;padding:24px">
  <h3>Bitrix app installation endpoint is reachable</h3>
  <p>Завершаем установку приложения...</p>
  <script>
    (function () {
      function finishInstall() {
        if (window.BX24 && typeof BX24.init === 'function') {
          BX24.init(function () {
            if (typeof BX24.installFinish === 'function') {
              BX24.installFinish();
            }
          });
        }
      }
      finishInstall();
      setTimeout(finishInstall, 1000);
    })();
  </script>
</body>
</html>
"""
    return HttpResponse(html)


def _flat_to_nested(flat: dict) -> dict:
    """Преобразует плоские ключи вида data[CONNECTOR], data[MESSAGES][0][chat][id] в вложенный dict."""
    result = {}
    for key, value in flat.items():
        if not isinstance(key, str) or '[' not in key:
            result[key] = value
            continue
        parts = re.split(r'\]?\[', key.replace(']', ''))
        d = result
        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
            if i == len(parts) - 1:
                d[part] = value
                break
            if part not in d:
                d[part] = {}
            d = d[part]
    return result


def _extract_event_name(payload: dict) -> str:
    event = (payload.get('event') or payload.get('EVENT') or '').strip()
    if event:
        return event
    data = payload.get('data') or payload.get('DATA') or {}
    if isinstance(data, dict):
        return (data.get('event') or '').strip()
    return ''


def _extract_event_data(payload: dict) -> dict:
    data = payload.get('data') or payload.get('DATA')
    if isinstance(data, dict):
        return data
    # Bitrix может слать form-urlencoded с ключами data[CONNECTOR], data[LINE], data[MESSAGES][0]...
    if any(k for k in payload if isinstance(k, str) and k.startswith('data[')):
        nested = _flat_to_nested(payload)
        return nested.get('data') or nested
    return payload


def _strip_bitrix_markup(text: str) -> str:
    """
    Удаляет базовую разметку Bitrix ([b], [i], [u], [br], [url=...]) для внешних каналов.
    """
    if not text:
        return ''
    cleaned = str(text)
    cleaned = re.sub(r'\[br\s*/?\]', '\n', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\[(?:/?)(?:b|i|u|s|code|quote)\]', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\[url=[^\]]+\]', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\[/url\]', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _max_webhook_secret_ok(request: HttpRequest) -> bool:
    """
    Проверка секрета webhook MAX, если он задан.
    """
    from django.conf import settings

    expected = (getattr(settings, 'MAX_WEBHOOK_SECRET', '') or '').strip()
    if not expected:
        return True
    provided = (
        request.headers.get('X-Max-Bot-Api-Secret')
        or request.headers.get('X-MAX-Webhook-Secret')
        or request.headers.get('X-Max-Webhook-Secret')
        or request.GET.get('secret')
        or ''
    ).strip()
    return provided == expected


def _max_group_webhook_secret_ok(request: HttpRequest) -> bool:
    """
    Проверка секрета webhook для создания групп MAX.
    """
    from django.conf import settings

    expected = (getattr(settings, 'MAX_GROUP_WEBHOOK_SECRET', '') or '').strip()
    if not expected:
        return True
    provided = (
        request.headers.get('X-Webhook-Secret')
        or request.headers.get('X-Max-Group-Secret')
        or request.GET.get('secret')
        or ''
    ).strip()
    return provided == expected


def _max_group_rate_limit_check() -> tuple[bool, int]:
    """
    Ограничение частоты создания MAX-групп.
    Возвращает (allowed, retry_after_sec).
    """
    from django.conf import settings

    min_interval_sec = int(getattr(settings, 'MAX_GROUP_MIN_INTERVAL_SEC', 0) or 0)
    if min_interval_sec <= 0:
        return True, 0

    base_dir = Path(getattr(settings, 'BASE_DIR'))
    profile_dir = base_dir / 'profile'
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock_path = profile_dir / '.max_group_rate.lock'
    stamp_path = profile_dir / '.max_group_last_create_ts'
    now_ts = int(time.time())

    with lock_path.open('w', encoding='utf-8') as lock_file:
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except Exception:
            # best-effort lock if flock is unavailable
            pass

        last_ts = 0
        if stamp_path.exists():
            try:
                last_ts = int((stamp_path.read_text(encoding='utf-8') or '0').strip() or 0)
            except Exception:
                last_ts = 0

        elapsed = max(0, now_ts - last_ts)
        if last_ts > 0 and elapsed < min_interval_sec:
            retry_after = min_interval_sec - elapsed
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            return False, retry_after

        stamp_path.write_text(str(now_ts), encoding='utf-8')
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass

    return True, 0


def _normalize_user_ids(raw_user_ids) -> list[int]:
    """
    Принимает user_ids как массив или CSV-строку и возвращает list[int].
    """
    if isinstance(raw_user_ids, list):
        items = raw_user_ids
    else:
        items = str(raw_user_ids or '').split(',')
    result: list[int] = []
    for item in items:
        value = str(item).strip()
        if not value:
            continue
        if not value.isdigit():
            continue
        result.append(int(value))
    # убираем дубли, сохраняя порядок
    dedup: list[int] = []
    seen: set[int] = set()
    for value in result:
        if value in seen:
            continue
        seen.add(value)
        dedup.append(value)
    return dedup


def _exclude_user_ids(user_ids: list[int], raw_excluded_ids) -> list[int]:
    """
    Исключает из списка user_ids значения из raw_excluded_ids (list или CSV-строка).
    """
    excluded = set(_normalize_user_ids(raw_excluded_ids))
    if not excluded:
        return user_ids
    return [user_id for user_id in user_ids if user_id not in excluded]


def _extract_bitrix_entity_ref(payload: dict) -> tuple[str, str]:
    """
    Извлекает сущность Bitrix из payload робота/БП.
    Возвращает (entity_type, entity_id), где entity_type: contact|company|''.
    """
    raw_doc = str(payload.get('document_id[2]') or payload.get('document_id') or '').strip()
    doc_upper = raw_doc.upper()
    if doc_upper.startswith('CONTACT_'):
        return 'contact', raw_doc.split('_', 1)[1].strip()
    if doc_upper.startswith('COMPANY_'):
        return 'company', raw_doc.split('_', 1)[1].strip()

    class_name = str(payload.get('document_id[1]') or '').strip()
    entity_id = str(payload.get('entity_id') or payload.get('id') or '').strip()
    if class_name == 'CCrmDocumentContact' and entity_id:
        return 'contact', entity_id
    if class_name == 'CCrmDocumentCompany' and entity_id:
        return 'company', entity_id
    return '', ''


def _extract_max_user_ids_from_contact(contact: dict) -> list[int]:
    """
    Извлекает MAX user_id из контактных IM-значений Bitrix (VALUE_TYPE=IMOL).
    Поддерживаем форматы:
    - max:g:<group_id>:u:<user_id>
    - max:dm:<user_id>
    - max:<user_id> (legacy)
    """
    im_values = contact.get('IM') or []
    if not isinstance(im_values, list):
        return []

    found: list[int] = []
    seen: set[int] = set()
    for item in im_values:
        if not isinstance(item, dict):
            continue
        value_type = str(item.get('VALUE_TYPE') or '').upper()
        value = str(item.get('VALUE') or '').strip()
        if value_type != 'IMOL' or not value:
            continue

        match_user = re.search(r'max:g:[^:]+:u:(\d+)', value)
        if match_user:
            user_id = int(match_user.group(1))
            if user_id not in seen:
                seen.add(user_id)
                found.append(user_id)
            continue

        match_dm = re.search(r'max:dm:(\d+)', value)
        if match_dm:
            user_id = int(match_dm.group(1))
            if user_id not in seen:
                seen.add(user_id)
                found.append(user_id)
            continue

        match_legacy = re.search(r'max:(\d+)$', value)
        if match_legacy:
            user_id = int(match_legacy.group(1))
            if user_id not in seen:
                seen.add(user_id)
                found.append(user_id)

    return found


def _extract_company_contact_ids(items) -> list[int]:
    """
    Нормализует список связей company->contacts из разных форматов ответа Bitrix.
    """
    result: list[int] = []
    seen: set[int] = set()

    rows = items if isinstance(items, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_contact_id = row.get('CONTACT_ID') or row.get('contactId') or row.get('ID') or row.get('id')
        contact_id = str(raw_contact_id or '').strip()
        if not contact_id.isdigit():
            continue
        parsed = int(contact_id)
        if parsed in seen:
            continue
        seen.add(parsed)
        result.append(parsed)
    return result


def _first_non_empty_text(*values) -> str:
    for value in values:
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return ''


def _value_to_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value).strip()
    if isinstance(value, dict):
        return _first_non_empty_text(
            str(value.get('VALUE') or '').strip(),
            str(value.get('value') or '').strip(),
            str(value.get('TITLE') or '').strip(),
            str(value.get('title') or '').strip(),
            str(value.get('NAME') or '').strip(),
            str(value.get('name') or '').strip(),
        )
    if isinstance(value, list):
        for item in value:
            text = _value_to_text(item)
            if text:
                return text
    return ''


def _normalize_group_prefix_number(value) -> str:
    text = _value_to_text(value)
    if not text:
        return ''
    normalized = text.replace(',', '.').strip()
    try:
        number = float(normalized)
    except (TypeError, ValueError):
        return normalized
    if number.is_integer():
        return str(int(number))
    return normalized


def _contact_name_region_from_entity(entity_type: str, entity_id: str) -> tuple[str, str, str, str]:
    normalized_type = str(entity_type or '').strip().lower()
    normalized_id = str(entity_id or '').strip()
    if normalized_type not in ('contact', 'company') or not normalized_id.isdigit():
        return '', '', '', ''

    try:
        from mailings.services.bitrix import call_bitrix, get_contact
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group title resolve: bitrix client import error: %s', exc)
        return '', '', '', ''

    contact = {}
    company = {}
    if normalized_type == 'contact':
        contact = get_contact(normalized_id) or {}
    else:
        try:
            company_response = call_bitrix('crm.company.get', {'id': normalized_id})
            company = company_response.get('result') or {}
        except Exception:
            try:
                company_response = call_bitrix('crm.company.get', {'ID': normalized_id})
                company = company_response.get('result') or {}
            except Exception:
                company = {}
        links = []
        try:
            response = call_bitrix('crm.company.contact.items.get', {'id': normalized_id})
            links = response.get('result') or []
        except Exception:
            try:
                response = call_bitrix('crm.company.contact.items.get', {'ID': normalized_id})
                links = response.get('result') or []
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    'MAX group title resolve failed: crm.company.contact.items.get error for company=%s: %s',
                    normalized_id,
                    exc,
                )
                links = []
        contact_ids = _extract_company_contact_ids(links)
        if contact_ids:
            contact = get_contact(contact_ids[0]) or {}

    last_name = _value_to_text(contact.get('LAST_NAME'))
    first_name = _value_to_text(contact.get('NAME'))
    region = _first_non_empty_text(
        _value_to_text(contact.get('UF_CRM_1747678283')),
        _value_to_text(contact.get('UF_CRM_1742561008637')),
        _value_to_text(contact.get('UF_CRM_680A1B15C9661')),
        _value_to_text(contact.get('UF_CRM_REGION')),
        _value_to_text(contact.get('ADDRESS_CITY')),
        _value_to_text(contact.get('REG_ADDRESS_CITY')),
        _value_to_text(contact.get('CITY')),
        _value_to_text(contact.get('REGION')),
        _value_to_text(company.get('UF_CRM_1747678283')),
        _value_to_text(company.get('UF_CRM_1742561008637')),
        _value_to_text(company.get('UF_CRM_680A1B15C9661')),
        _value_to_text(company.get('ADDRESS_CITY')),
        _value_to_text(company.get('REG_ADDRESS_CITY')),
        _value_to_text(company.get('ADDRESS')),
        _value_to_text(company.get('REG_ADDRESS')),
        _value_to_text(company.get('ADDRESS_LEGAL')),
    )
    contact_chat_field = str(
        getattr(settings, 'BITRIX_UF_MESSENGER_CHAT_NUMBER', 'UF_CRM_1742560088') or 'UF_CRM_1742560088'
    ).strip()
    company_chat_field = str(
        getattr(settings, 'BITRIX_UF_COMPANY_MESSENGER_CHAT_NUMBER', 'UF_CRM_1779436763') or 'UF_CRM_1779436763'
    ).strip()

    group_number = ''
    if normalized_type == 'company':
        group_number = _normalize_group_prefix_number(company.get(company_chat_field))
    if not group_number:
        group_number = _normalize_group_prefix_number(contact.get(contact_chat_field))
    if not group_number and normalized_type == 'company':
        # Фолбэк на контактное поле (на случай старых данных в компании).
        group_number = _normalize_group_prefix_number(company.get(contact_chat_field))
    return group_number, last_name, first_name, region


def _build_generated_group_title(payload: dict, entity_type: str, entity_id: str) -> str:
    group_number = _first_non_empty_text(
        str(payload.get('group_number') or '').strip(),
        str(payload.get('messenger_chat_number') or '').strip(),
        str(payload.get('chat_number') or '').strip(),
    )
    prefix = f'{group_number} / LACONISTIQ / DECOPLUME'

    last_name = _first_non_empty_text(
        str(payload.get('last_name') or '').strip(),
        str(payload.get('surname') or '').strip(),
        str(payload.get('lastname') or '').strip(),
    )
    first_name = _first_non_empty_text(
        str(payload.get('first_name') or '').strip(),
        str(payload.get('name') or '').strip(),
    )
    region = _first_non_empty_text(
        str(payload.get('region') or '').strip(),
        str(payload.get('city') or '').strip(),
    )

    if not (group_number and last_name and first_name and region):
        resolved_group_number, resolved_last_name, resolved_first_name, resolved_region = _contact_name_region_from_entity(
            entity_type,
            entity_id,
        )
        group_number = group_number or resolved_group_number
        last_name = last_name or resolved_last_name
        first_name = first_name or resolved_first_name
        region = region or resolved_region

    if not group_number:
        group_number = str(random.randint(1000, 9999))
    prefix = f'{group_number} / LACONISTIQ / DECOPLUME'

    full_name = ' '.join(part for part in [last_name, first_name] if part).strip()
    if full_name:
        title = f'{prefix} {full_name} партнер'
    else:
        title = f'{prefix} партнер'
    if region:
        title = f'{title}, {region}'
    title = f'{title} (Профиль, Плинтус, Панели)'
    return title[:255]


def _resolve_max_user_ids_from_entity(entity_type: str, entity_id: str) -> list[int]:
    """
    Резолвит user_ids для создания MAX-группы по Bitrix сущности (IMOL).
    - contact: IMOL из карточки контакта
    - company: ответственный + сотрудники из админ-справочника; MAILING-телефоны — в Celery
    """
    normalized_type = str(entity_type or '').strip().lower()
    normalized_id = str(entity_id or '').strip()
    if not normalized_id.isdigit():
        return []

    if normalized_type == 'contact':
        from .max_group_members import resolve_contact_group_user_ids

        return resolve_contact_group_user_ids(normalized_id)

    if normalized_type == 'company':
        from .max_group_members import resolve_company_group_user_ids

        # Для компании добавляем связанного сотрудника (ASSIGNED_BY_ID),
        # а телефоны MAILING остаются fallback в Celery-задаче.
        return resolve_company_group_user_ids(normalized_id)

    return []


def _ensure_max_partner(chat_id: str, chat_type: str, title: str) -> None:
    """
    Синхронизирует чат MAX в справочник Partner для рассылок из админки.
    """
    if not chat_id:
        return
    try:
        from mailings.models import Partner

        partner_chat_id = f'max:{chat_id}'
        partner_type = Partner.TYPE_PRIVATE
        normalized_type = (chat_type or '').lower()
        if normalized_type in ('group', 'supergroup'):
            partner_type = Partner.TYPE_GROUP
        elif normalized_type in ('channel',):
            partner_type = Partner.TYPE_CHANNEL

        Partner.objects.update_or_create(
            chat_id=partner_chat_id,
            defaults={
                'chat_type': partner_type,
                'title': (title or partner_chat_id)[:255],
                'is_active': True,
            },
        )
    except Exception as exc:
        logger.warning('MAX partner sync failed for chat_id=%s: %s', chat_id, exc)


def _extract_max_text(payload: dict) -> tuple[str, str, str, str]:
    """
    Нормализация входящего события MAX до (chat_id, chat_type, title, text).
    Поддерживаем несколько форматов payload, т.к. структура может отличаться.
    """
    message = payload.get('message') or {}
    body = message.get('body') or {}
    recipient = message.get('recipient') or {}
    sender = message.get('sender') or {}
    chat = payload.get('chat') or {}
    user = payload.get('user') or {}

    chat_id_candidates = {
        'recipient.chat_id': recipient.get('chat_id'),
        'recipient.chatId': recipient.get('chatId'),
        'recipient.id': recipient.get('id'),
        'recipient.chat.chat_id': (recipient.get('chat') or {}).get('chat_id'),
        'recipient.chat.id': (recipient.get('chat') or {}).get('id'),
        'payload.chat.id': chat.get('id'),
        'payload.chat_id': payload.get('chat_id'),
        'payload.chatId': payload.get('chatId'),
        'message.chat_id': message.get('chat_id'),
        'message.chatId': message.get('chatId'),
    }
    chat_id = (
        recipient.get('chat_id')
        or recipient.get('chatId')
        or recipient.get('id')
        or (recipient.get('chat') or {}).get('chat_id')
        or (recipient.get('chat') or {}).get('id')
        or chat.get('id')
        or payload.get('chat_id')
        or payload.get('chatId')
        or message.get('chat_id')
        or message.get('chatId')
    )
    text_candidates = {
        'message.body.text': body.get('text'),
        'message.text': message.get('text'),
        'payload.text': payload.get('text'),
        'message.content.text': (message.get('content') or {}).get('text'),
        'payload.body.text': (payload.get('body') or {}).get('text'),
    }
    text = (
        body.get('text')
        or message.get('text')
        or payload.get('text')
        or (message.get('content') or {}).get('text')
        or (payload.get('body') or {}).get('text')
        or ''
    )
    title_candidates = {
        'recipient.title': recipient.get('title'),
        'recipient.name': recipient.get('name'),
        'recipient.chat.title': (recipient.get('chat') or {}).get('title'),
        'recipient.chat.name': (recipient.get('chat') or {}).get('name'),
        'sender.name': sender.get('name'),
        'sender.username': sender.get('username'),
        'payload.chat.title': chat.get('title'),
        'payload.chat.name': chat.get('name'),
        'payload.user.name': user.get('name'),
    }
    title = (
        recipient.get('title')
        or recipient.get('name')
        or (recipient.get('chat') or {}).get('title')
        or (recipient.get('chat') or {}).get('name')
        or sender.get('name')
        or sender.get('username')
        or chat.get('title')
        or chat.get('name')
        or user.get('name')
        or str(chat_id or '')
    )
    chat_type_candidates = {
        'recipient.chat_type': recipient.get('chat_type'),
        'recipient.type': recipient.get('type'),
        'recipient.chat.type': (recipient.get('chat') or {}).get('type'),
        'payload.chat.type': chat.get('type'),
        'payload.chat_type': payload.get('chat_type'),
    }
    chat_type = (
        recipient.get('chat_type')
        or recipient.get('type')
        or (recipient.get('chat') or {}).get('type')
        or chat.get('type')
        or payload.get('chat_type')
        or 'private'
    )
    logger.info(
        'MAX extract candidates: chat_id=%s chat_type=%s title=%s text_len=%s selected={chat_id:%s chat_type:%s title:%s}',
        chat_id_candidates,
        chat_type_candidates,
        title_candidates,
        {k: len(str(v)) for k, v in text_candidates.items() if v},
        chat_id,
        chat_type,
        title,
    )
    return str(chat_id or ''), str(chat_type), str(title), str(text).strip()


def _extract_max_sender(payload: dict) -> tuple[str, str]:
    """
    Извлекает отправителя сообщения в MAX: (sender_id, sender_name).
    """
    message = payload.get('message') or {}
    sender = message.get('sender') or {}
    sender_id = (
        sender.get('user_id')
        or sender.get('id')
        or payload.get('user_id')
        or ''
    )
    sender_name = (
        sender.get('name')
        or ' '.join(
            part for part in [sender.get('first_name'), sender.get('last_name')] if isinstance(part, str) and part.strip()
        ).strip()
        or sender.get('username')
        or ''
    )
    return str(sender_id or ''), str(sender_name or '')


def _max_group_bitrix_sync_events() -> set[str]:
    raw = str(getattr(settings, 'MAX_GROUP_BITRIX_SYNC_EVENTS', 'bot_added,chat_title_changed') or '')
    return {item.strip().lower() for item in raw.split(',') if item.strip()}


def _try_queue_max_group_bitrix_sync(payload: dict) -> JsonResponse | None:
    """
    bot_added / chat_title_changed -> Celery: связать группу с контактом Bitrix по номеру в title.
    """
    if not bool(getattr(settings, 'MAX_GROUP_BITRIX_SYNC_ENABLED', True)):
        return None

    update_type = str(payload.get('update_type') or '').strip().lower()
    if update_type not in _max_group_bitrix_sync_events():
        return None

    if update_type == 'bot_added' and bool(payload.get('is_channel')):
        logger.info('MAX group bitrix sync ignored: bot_added to channel')
        return JsonResponse({'result': 'ignored', 'reason': 'channel'})

    chat_id = str(
        payload.get('chat_id')
        or payload.get('chatId')
        or (payload.get('chat') or {}).get('id')
        or ''
    ).strip()
    if not chat_id:
        logger.warning('MAX group bitrix sync ignored: empty chat_id update_type=%s', update_type)
        return JsonResponse({'result': 'ignored', 'reason': 'empty_chat_id'})

    title = ''
    if update_type == 'chat_title_changed':
        title = str(
            payload.get('title')
            or payload.get('chat_title')
            or (payload.get('chat') or {}).get('title')
            or ''
        ).strip()

    from .tasks import sync_max_group_from_bot_event

    task = sync_max_group_from_bot_event.delay(
        chat_id=chat_id,
        title=title,
        update_type=update_type,
    )
    logger.info(
        'MAX group bitrix sync queued: task_id=%s chat_id=%s update_type=%s title=%s',
        task.id,
        chat_id,
        update_type,
        title[:200],
    )
    return JsonResponse({'result': 'sync_queued', 'task_id': str(task.id), 'update_type': update_type})


@csrf_exempt
@require_http_methods(['POST'])
def max_handler(request: HttpRequest) -> HttpResponse:
    """
    Webhook MAX: bot_added/chat_title_changed -> связка группы с Bitrix;
    message_created -> OpenLines.
    """
    if not _max_webhook_secret_ok(request):
        return JsonResponse({'error': 'forbidden'}, status=403)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        payload = {}

    logger.info(
        'MAX webhook raw body: %s',
        (request.body or b'')[:3000].decode('utf-8', errors='replace'),
    )
    logger.info(
        'MAX webhook received: keys=%s update_type=%s',
        list(payload.keys()),
        payload.get('update_type'),
    )

    sync_response = _try_queue_max_group_bitrix_sync(payload)
    if sync_response is not None:
        return sync_response

    chat_id, chat_type, title, text = _extract_max_text(payload)
    sender_id, sender_name = _extract_max_sender(payload)
    # Для групп/каналов тянем актуальное название чата из MAX API.
    if chat_id and (chat_type in ('chat', 'channel', 'group', 'supergroup') or str(chat_id).startswith('-')):
        try:
            from mailings.services.max import get_chat_title as max_get_chat_title
            resolved_title = max_get_chat_title(chat_id)
            if resolved_title:
                title = resolved_title
        except Exception as exc:
            logger.debug('MAX chat title resolve failed for chat_id=%s: %s', chat_id, exc)
    logger.info(
        'MAX normalized: chat_id=%s chat_type=%s title=%s sender_id=%s sender_name=%s text_len=%s text_preview=%s',
        chat_id,
        chat_type,
        title,
        sender_id,
        sender_name,
        len(text or ''),
        (text or '')[:300],
    )
    if not chat_id or not text:
        logger.info(
            'MAX webhook ignored: chat_id=%s text_len=%s payload=%s',
            chat_id,
            len(text or ''),
            json.dumps(payload, ensure_ascii=False)[:1500],
        )
        return JsonResponse({'result': 'ignored'})

    from .services import send_max_message_to_openline

    _ensure_max_partner(chat_id=chat_id, chat_type=chat_type, title=title)
    send_max_message_to_openline(
        max_chat_id=chat_id,
        max_chat_type=chat_type,
        max_chat_title=title,
        text=text,
        max_sender_id=sender_id or chat_id,
        max_sender_name=sender_name or title,
    )
    logger.info('MAX -> OpenLines dispatched: chat_id=%s title=%s text_len=%s', chat_id, title, len(text or ''))
    return JsonResponse({'result': 'ok'})


@csrf_exempt
@require_http_methods(['POST'])
def max_create_group_webhook(request: HttpRequest) -> HttpResponse:
    """
    Входящий webhook для постановки Celery-задачи создания группы в MAX.
    Ожидает user_ids (list[int] или CSV строка). title можно передать явно,
    иначе сгенерируем автоматически по шаблону.
    """
    if not _max_group_webhook_secret_ok(request):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
    rate_allowed, retry_after_sec = _max_group_rate_limit_check()
    if not rate_allowed:
        return JsonResponse(
            {
                'ok': False,
                'error': f'rate_limited: retry in {retry_after_sec}s',
                'retry_after_sec': retry_after_sec,
            },
            status=429,
        )

    payload: dict = {}
    if (request.content_type or '').startswith('application/json'):
        try:
            parsed = json.loads(request.body.decode('utf-8') or '{}')
            if isinstance(parsed, dict):
                payload.update(parsed)
        except json.JSONDecodeError:
            pass
    # Для Bitrix-робота важны и form-поля, и query-string — собираем оба источника.
    payload.update(request.POST.dict())
    payload.update(request.GET.dict())

    title = str(payload.get('title') or '').strip()
    user_ids = _normalize_user_ids(payload.get('user_ids') or payload.get('users') or '')
    exclude_user_ids = payload.get('exclude_user_ids') or payload.get('exclude_users') or ''
    source = str(payload.get('source') or 'bitrix_webhook').strip()
    request_id = str(payload.get('request_id') or '').strip()
    contact_email = str(payload.get('email') or '').strip()
    avatar_url = str(payload.get('avatar_url') or payload.get('group_avatar_url') or '').strip()
    entity_type, entity_id = _extract_bitrix_entity_ref(payload)
    explicit_entity_type = str(payload.get('entity_type') or payload.get('entity') or '').strip().lower()
    explicit_entity_id = str(
        payload.get('entity_id')
        or payload.get('company_id')
        or payload.get('contact_id')
        or ''
    ).strip()
    if explicit_entity_type:
        entity_type = explicit_entity_type
    if explicit_entity_id:
        entity_id = explicit_entity_id

    from django.conf import settings
    configured_excluded = getattr(settings, 'MAX_GROUP_EXCLUDE_USER_IDS', '')
    user_ids = _exclude_user_ids(user_ids, configured_excluded)
    user_ids = _exclude_user_ids(user_ids, exclude_user_ids)
    bot_user_id = int(getattr(settings, 'MAX_GROUP_BOT_USER_ID', 0) or 0)
    allow_empty_with_bot = bool(getattr(settings, 'MAX_GROUP_ALLOW_EMPTY_WITH_BOT', False))

    if not title:
        title = _build_generated_group_title(payload=payload, entity_type=entity_type, entity_id=entity_id)
        logger.info(
            'MAX create-group generated title: title=%s entity=%s:%s',
            title,
            entity_type,
            entity_id,
        )
    if entity_type in ('contact', 'company') and entity_id:
        resolved_entity_ids = _resolve_max_user_ids_from_entity(entity_type, entity_id)
        merged_user_ids = _normalize_user_ids(user_ids + resolved_entity_ids)
        user_ids = _exclude_user_ids(merged_user_ids, configured_excluded)
        user_ids = _exclude_user_ids(user_ids, exclude_user_ids)
        logger.info(
            'MAX create-group merged user_ids for %s: entity_id=%s incoming=%s resolved=%s total=%s',
            entity_type,
            entity_id,
            len(_normalize_user_ids(payload.get('user_ids') or payload.get('users') or '')),
            len(resolved_entity_ids),
            len(user_ids),
        )
    elif not user_ids and entity_type and entity_id:
        user_ids = _resolve_max_user_ids_from_entity(entity_type, entity_id)
        user_ids = _exclude_user_ids(user_ids, configured_excluded)
        user_ids = _exclude_user_ids(user_ids, exclude_user_ids)
        logger.info(
            'MAX create-group resolved user_ids from entity: type=%s id=%s count=%s',
            entity_type,
            entity_id,
            len(user_ids),
        )
    if not user_ids:
        from .entity_phones import resolve_company_group_phones, resolve_entity_phones

        company_phones = (
            resolve_company_group_phones(entity_id)
            if entity_type == 'company' and entity_id
            else []
        )
        contact_phones = (
            resolve_entity_phones(entity_type='contact', entity_id=entity_id)
            if entity_type == 'contact' and entity_id
            else []
        )
        if allow_empty_with_bot and bot_user_id > 0:
            user_ids = [bot_user_id]
            logger.info(
                'MAX create-group fallback: no resolved users, using bot as initial member (bot_user_id=%s)',
                bot_user_id,
            )
        elif company_phones:
            logger.info(
                'MAX create-group: company=%s has PHONE(s) %s, user_id resolve deferred to task',
                entity_id,
                company_phones,
            )
        elif contact_phones:
            logger.info(
                'MAX create-group: contact=%s has PHONE(s) %s, user_id resolve deferred to task',
                entity_id,
                contact_phones,
            )
        else:
            return JsonResponse(
                {
                    'ok': False,
                    'error': (
                        'No eligible user_ids after validation/exclusion '
                        '(provide user_ids, contact IMOL/PHONE, or company MAILING PHONE)'
                    ),
                },
                status=400,
            )

    from .tasks import create_max_group_from_webhook

    task = create_max_group_from_webhook.delay(
        title=title,
        user_ids=user_ids,
        source=source,
        request_id=request_id,
        entity_type=entity_type,
        entity_id=entity_id,
        contact_email=contact_email,
        avatar_url=avatar_url,
    )
    return JsonResponse(
        {
            'ok': True,
            'queued': True,
            'task_id': task.id,
            'title': title,
            'user_ids_count': len(user_ids),
            'entity_type': entity_type,
            'entity_id': entity_id,
        },
        status=202,
    )


@csrf_exempt
@require_http_methods(['POST', 'GET'])
def max_create_group_debug_webhook(request: HttpRequest) -> HttpResponse:
    """
    Временная заглушка для отладки интеграции с Bitrix:
    принимает любой payload, пишет его в логи/консоль и возвращает ok.
    """
    if not _max_group_webhook_secret_ok(request):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    payload: dict = {}
    if request.method == 'POST' and (request.content_type or '').startswith('application/json'):
        try:
            parsed = json.loads(request.body.decode('utf-8') or '{}')
            if isinstance(parsed, dict):
                payload.update(parsed)
        except json.JSONDecodeError:
            pass
    # В debug-режиме показываем объединённый payload: body/form/query.
    payload.update(request.POST.dict())
    payload.update(request.GET.dict())

    logger.info('MAX create-group debug webhook payload: %s', payload)
    print('[MAX create-group debug webhook]', json.dumps(payload, ensure_ascii=False, indent=2))

    return JsonResponse(
        {
            'ok': True,
            'debug': True,
            'method': request.method,
            'received_keys': sorted(list(payload.keys())),
        }
    )


@csrf_exempt
@require_http_methods(['POST', 'GET'])
def bitrix_handler(request: HttpRequest) -> HttpResponse:
    """
    Главная точка входа для локального серверного приложения Bitrix24.

    Сценарии:
    - первоначальная установка/проверка приложения;
    - системные запросы Битрикса (пинг, события и т.д.).

    Сейчас:
    - если в теле/GET есть блок auth[...] — обновляем/создаём запись портала и сохраняем токены;
    - логируем входящий payload;
    - возвращаем {"result": "ok"}.

    Дальше сюда же можно будет добавить обработку конкретных событий (например, сообщений ОЛ).
    """
    try:
        payload: dict = {}
        if request.method == 'POST':
            if request.content_type == 'application/json':
                try:
                    payload = json.loads(request.body.decode('utf-8') or '{}')
                except json.JSONDecodeError:
                    payload = {}
            else:
                # x-www-form-urlencoded
                payload = {**request.POST.dict()}
                # auth[...] Bitrix может класть как auth[access_token], так и отдельными полями
                auth = {}
                for key, value in request.POST.items():
                    if key.startswith('auth[') and key.endswith(']'):
                        auth_key = key[5:-1]
                        auth[auth_key] = value
                # Локальное приложение часто шлёт AUTH_ID/REFRESH_ID/CLIENT_ENDPOINT/AUTH_EXPIRES
                if 'AUTH_ID' in request.POST:
                    auth.setdefault('access_token', request.POST['AUTH_ID'])
                if 'REFRESH_ID' in request.POST:
                    auth.setdefault('refresh_token', request.POST['REFRESH_ID'])
                if 'client_endpoint' in request.POST:
                    auth.setdefault('client_endpoint', request.POST['client_endpoint'])
                # expires_in либо приходит явно, либо как AUTH_EXPIRES
                if 'expires_in' in request.POST:
                    auth.setdefault('expires_in', request.POST['expires_in'])
                elif 'AUTH_EXPIRES' in request.POST:
                    auth.setdefault('expires_in', request.POST['AUTH_EXPIRES'])
                if auth:
                    payload['auth'] = auth
                # Для отладки: выведем, что именно пришло от Битрикса
                print('[Bitrix OpenLines POST]', dict(request.POST))
        else:
            payload = {**request.GET.dict()}

        logger.info(
            'Bitrix handler: method=%s content_type=%s body_len=%s',
            request.method,
            request.content_type or '',
            len(request.body) if request.body else 0,
        )
        if request.body and (request.content_type or '').strip().startswith('application/json'):
            logger.info('Bitrix handler body: %s', request.body[:1500].decode('utf-8', errors='replace'))
        elif request.method == 'POST' and request.POST:
            logger.info('Bitrix handler POST keys: %s', list(request.POST.keys())[:30])

        logger.info('Bitrix OpenLines handler request: %s', payload)
        print('[Bitrix OpenLines payload]', json.dumps(payload, ensure_ascii=False, indent=2))

        if 'auth' in payload:
            try:
                portal = upsert_portal_from_auth_payload(payload)
                logger.info('Bitrix portal tokens updated for %s', portal.portal_url)
            except BitrixAuthError as e:
                logger.warning('Failed to update Bitrix portal tokens: %s', e)

        # Обработка событий коннектора Открытых линий: ответы оператора → Telegram
        event_name = _extract_event_name(payload)
        logger.info('Bitrix event detected: %s', event_name or '<empty>')
        if event_name.upper() == 'ONIMCONNECTORMESSAGEADD':
            data = _extract_event_data(payload)
            connector_id = (data.get('CONNECTOR') or data.get('connector') or '').strip()
            line_id = data.get('LINE') or data.get('line')
            raw_messages = data.get('MESSAGES') or data.get('messages') or []

            logger.info(
                'OnImConnectorMessageAdd: CONNECTOR=%s LINE=%s raw_messages type=%s',
                connector_id, line_id, type(raw_messages).__name__,
            )

            # MESSAGES может прийти как list или как dict с ключами "0", "1" (form-urlencoded)
            if isinstance(raw_messages, dict):
                messages = []
                for k in sorted(raw_messages.keys(), key=lambda x: int(x) if str(x).isdigit() else -1):
                    v = raw_messages.get(k)
                    if isinstance(v, dict):
                        messages.append(v)
            elif isinstance(raw_messages, list):
                messages = raw_messages
            else:
                messages = []

            from django.conf import settings

            expected_connector = getattr(settings, 'BITRIX_OPENLINE_CONNECTOR_ID', '').strip()
            expected_line = getattr(settings, 'BITRIX_OPENLINE_LINE_ID', '').strip()
            if expected_connector and connector_id and connector_id != expected_connector:
                logger.info('Skip OnImConnectorMessageAdd for other connector: %s', connector_id)
                return JsonResponse({'result': 'ignored'})
            if expected_line and str(line_id) != expected_line:
                logger.info('Skip OnImConnectorMessageAdd for other line: %s', line_id)
                return JsonResponse({'result': 'ignored'})

            if not messages:
                logger.warning(
                    'OnImConnectorMessageAdd: no messages to send, data keys=%s raw_messages sample=%s',
                    list(data.keys()),
                    str(raw_messages)[:500],
                )

            # Отправляем ответы оператора во внешний канал по chat.id.
            from mailings.services.telegram import send_message as tg_send_message
            from mailings.services.max import (
                send_message as max_send_message,
                send_media_message_by_url as max_send_media_message_by_url,
                MaxApiError,
            )

            delivered: list[dict] = []
            from openlines.models import ExternalChat

            for idx, item in enumerate(messages):
                if not isinstance(item, dict):
                    logger.warning('OnImConnectorMessageAdd message[%s] not dict: %s', idx, type(item))
                    continue
                chat = item.get('chat') or {}
                message = item.get('message') or {}
                text = _strip_bitrix_markup((message.get('text') or '').strip())
                external_chat_id = chat.get('id')
                im = item.get('im') or {}
                im_chat_id = im.get('chat_id')
                raw_files = message.get('files') or []
                logger.info(
                    'OnImConnectorMessageAdd message[%s]: chat_id=%s im_chat_id=%s text_len=%s files_type=%s',
                    idx, external_chat_id, im_chat_id, len(text), type(raw_files).__name__,
                )
                files: list[dict] = []
                if isinstance(raw_files, dict):
                    for k in sorted(raw_files.keys(), key=lambda x: int(x) if str(x).isdigit() else -1):
                        v = raw_files.get(k)
                        if isinstance(v, dict):
                            files.append(v)
                elif isinstance(raw_files, list):
                    files = [v for v in raw_files if isinstance(v, dict)]
                has_files = bool(files)
                if not text and not has_files:
                    logger.warning('OnImConnectorMessageAdd message[%s]: skip (no text and no files)', idx)
                    continue
                if not external_chat_id and im_chat_id:
                    # Fallback: восстанавливаем внешний chat_id по связи с Bitrix chat_id.
                    mapped = ExternalChat.objects.filter(bitrix_chat_id=str(im_chat_id)).order_by('-updated_at').first()
                    if mapped:
                        external_chat_id = mapped.telegram_chat_id
                        logger.info(
                            'OnImConnectorMessageAdd message[%s]: resolved external chat by im.chat_id=%s -> %s',
                            idx, im_chat_id, external_chat_id,
                        )
                if not external_chat_id:
                    logger.warning(
                        'OnImConnectorMessageAdd message[%s]: skip (no chat_id and no mapping for im_chat_id=%s)',
                        idx, im_chat_id,
                    )
                    continue
                external_chat_id_str = str(external_chat_id)
                if external_chat_id_str.startswith('max:'):
                    max_target_id = ''
                    max_target_kind = 'legacy'
                    if external_chat_id_str.startswith('max:g:'):
                        # Формат: max:g:<group_id>:u:<sender_id>
                        parts = external_chat_id_str.split(':')
                        if len(parts) >= 5 and parts[1] == 'g' and parts[3] == 'u':
                            max_target_kind = 'group_user'
                            max_target_id = parts[2]
                        else:
                            max_target_kind = 'group_user_invalid'
                    elif external_chat_id_str.startswith('max:group:'):
                        max_target_kind = 'group'
                        max_target_id = external_chat_id_str.split(':', 2)[2]
                    elif external_chat_id_str.startswith('max:dm:'):
                        max_target_kind = 'dm'
                        max_target_id = external_chat_id_str.split(':', 2)[2]
                    else:
                        max_target_id = external_chat_id_str.split(':', 1)[1]

                    max_target_id = str(max_target_id or '').strip()
                    if not max_target_id:
                        logger.warning('OnImConnectorMessageAdd message[%s]: empty MAX target for %s', idx, external_chat_id_str)
                        continue
                    try:
                        if has_files:
                            file_item = files[0]
                            file_url = (
                                file_item.get('downloadLink')
                                or file_item.get('link')
                                or ''
                            )
                            file_type = (file_item.get('type') or '').lower()
                            if file_type == 'image':
                                send_type = 'photo'
                            elif file_type == 'video':
                                send_type = 'video'
                            else:
                                send_type = 'document'
                            if not file_url:
                                result = {'ok': False, 'error': 'Bitrix file has no URL'}
                            else:
                                try:
                                    result = max_send_media_message_by_url(
                                        max_target_id,
                                        file_url=file_url,
                                        send_type=send_type,
                                        text=text,
                                    )
                                except MaxApiError:
                                    # Fallback: если upload в MAX не принял файл, отправляем ссылку.
                                    link_text = (text + "\n\n" if text else "") + f"Файл: {file_url}"
                                    result = max_send_message(max_target_id, link_text)
                        else:
                            result = max_send_message(max_target_id, text)
                    except MaxApiError as exc:
                        result = {'ok': False, 'error': str(exc)}
                    logger.info(
                        'OnImConnectorMessageAdd message[%s]: MAX route=%s target=%s source=%s',
                        idx,
                        max_target_kind,
                        max_target_id,
                        external_chat_id_str,
                    )
                else:
                    result = tg_send_message(external_chat_id_str, text, parse_mode='Markdown')
                if result.get('ok'):
                    delivered.append({'im': item.get('im') or {}, 'chat': {'id': external_chat_id_str}})
                    logger.info('External channel sent to chat_id=%s', external_chat_id)
                else:
                    logger.warning('External channel send failed for chat_id=%s: %s', external_chat_id, result)

            # Подтверждаем доставку в Bitrix, чтобы не было "сообщение не доставлено"
            try:
                if connector_id and line_id and delivered:
                    send_status_delivery(connector_id=connector_id, line_id=line_id, messages=delivered)
                    logger.info('Bitrix delivery status sent for %s messages', len(delivered))
                else:
                    logger.warning('Bitrix delivery not sent: connector=%s line=%s delivered=%s', connector_id, line_id, len(delivered))
            except Exception as exc:
                logger.warning('Failed to send delivery status to Bitrix: %s', exc, exc_info=True)

        return JsonResponse({'result': 'ok'})
    except Exception as e:
        logger.exception('Bitrix handler error: %s', e)
        return JsonResponse({'error': str(e)}, status=500)

