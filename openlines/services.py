import datetime as dt
import logging
import time
from typing import Any, Dict

import requests
from django.utils import timezone

from .models import BitrixPortal, ExternalChat

logger = logging.getLogger(__name__)


class BitrixAuthError(Exception):
    pass


def get_default_portal() -> BitrixPortal:
    """
    Получить (или создать) запись портала по данным из settings/env.
    Предполагаем один портал на проект.
    """
    from django.conf import settings

    portal_url = getattr(settings, 'BITRIX_PORTAL_URL', '').strip()
    client_id = getattr(settings, 'BITRIX_APP_CLIENT_ID', '').strip()
    client_secret = getattr(settings, 'BITRIX_APP_CLIENT_SECRET', '').strip()
    if not portal_url or not client_id or not client_secret:
        raise BitrixAuthError(
            'BITRIX_PORTAL_URL / BITRIX_APP_CLIENT_ID / BITRIX_APP_CLIENT_SECRET не заданы в настройках/.env'
        )
    portal, _ = BitrixPortal.objects.get_or_create(
        portal_url=portal_url,
        defaults={
            'name': portal_url,
            'client_id': client_id,
            'client_secret': client_secret,
        },
    )
    changed = False
    if portal.client_id != client_id:
        portal.client_id = client_id
        changed = True
    if portal.client_secret != client_secret:
        portal.client_secret = client_secret
        changed = True
    if changed:
        portal.save(update_fields=['client_id', 'client_secret', 'updated_at'])
    return portal


def _refresh_access_token(portal: BitrixPortal) -> None:
    """
    Обновить access_token по refresh_token. Бросает BitrixAuthError при ошибке.
    """
    if not portal.refresh_token:
        raise BitrixAuthError('Нет refresh_token для обновления access_token')

    url = 'https://oauth.bitrix.info/oauth/token/'
    data = {
        'grant_type': 'refresh_token',
        'client_id': portal.client_id,
        'client_secret': portal.client_secret,
        'refresh_token': portal.refresh_token,
    }
    resp = requests.post(url, data=data, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if 'access_token' not in payload:
        raise BitrixAuthError(f'Не удалось обновить токен Bitrix: {payload}')

    portal.access_token = payload['access_token']
    portal.refresh_token = payload.get('refresh_token', portal.refresh_token)
    expires_in = int(payload.get('expires_in') or 0)
    if expires_in:
        portal.access_token_expires_at = timezone.now() + dt.timedelta(seconds=expires_in - 60)
    portal.save(update_fields=['access_token', 'refresh_token', 'access_token_expires_at', 'updated_at'])
    logger.info('Bitrix access_token обновлён для портала %s', portal.portal_url)


def _ensure_valid_token(portal: BitrixPortal) -> None:
    """
    Проверить, что access_token актуален, и при необходимости обновить.
    """
    if not portal.access_token:
        raise BitrixAuthError('Access token Bitrix не задан. Установите приложение в портале.')
    if portal.access_token_expires_at and portal.access_token_expires_at <= timezone.now():
        _refresh_access_token(portal)


def call_bitrix_rest(method: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Вызов REST Bitrix24 под приложением (через OAuth-токен), не через исходящий вебхук.
    method — строка вида 'crm.contact.add', 'imconnector.send.messages' и т.д.
    """
    portal = get_default_portal()
    _ensure_valid_token(portal)

    url = f'{portal.portal_url.rstrip("/")}/rest/{method}'
    params = params or {}
    params_with_auth = {'auth': portal.access_token, **params}

    # В REST Bitrix вложенные структуры (ICON, MESSAGES, ...) корректнее передавать JSON'ом.
    resp = requests.post(url, json=params_with_auth, timeout=30)

    try:
        data = resp.json()
    except Exception:
        data = {}

    if not resp.ok:
        msg = (
            data.get('error_description') or data.get('error') or resp.reason or resp.text or str(resp.status_code)
        )
        if isinstance(msg, dict):
            msg = str(msg)
        raise BitrixAuthError(f"Bitrix REST {resp.status_code}: {msg}")

    if isinstance(data, dict) and data.get('error'):
        raise BitrixAuthError(f"Bitrix REST error: {data.get('error_description', data.get('error'))}")
    return data


def upsert_portal_from_auth_payload(payload: dict) -> BitrixPortal:
    """
    Обновить/создать запись портала по данным auth[...] из запроса Bitrix.

    Для локального приложения Bitrix часто шлёт только AUTH_ID/REFRESH_ID без client_endpoint,
    поэтому URL портала берём из настроек (get_default_portal), а не из payload.
    """
    auth = payload.get('auth') or {}
    access_token = auth.get('access_token') or ''
    refresh_token = auth.get('refresh_token') or ''
    expires_in = int(auth.get('expires_in') or 0)

    if not access_token:
        raise BitrixAuthError('В payload нет access_token')

    portal = get_default_portal()

    portal.access_token = access_token
    if refresh_token:
        portal.refresh_token = refresh_token
    if expires_in:
        portal.access_token_expires_at = timezone.now() + dt.timedelta(seconds=expires_in - 60)
    portal.save(update_fields=['access_token', 'refresh_token', 'access_token_expires_at', 'updated_at'])

    return portal


def _get_openline_connector_and_line() -> tuple[str, str]:
    """
    Получить идентификаторы коннектора и линии для imconnector.send.messages.
    """
    from django.conf import settings

    connector = getattr(settings, 'BITRIX_OPENLINE_CONNECTOR_ID', '').strip()
    line = getattr(settings, 'BITRIX_OPENLINE_LINE_ID', '').strip()
    if not connector or not line:
        raise BitrixAuthError(
            'BITRIX_OPENLINE_CONNECTOR_ID / BITRIX_OPENLINE_LINE_ID не заданы в настройках/.env'
        )
    return connector, line


def get_or_create_external_chat_for_telegram(
    portal: BitrixPortal,
    telegram_chat_id: str,
    telegram_chat_type: str,
    telegram_chat_title: str,
) -> ExternalChat:
    """
    Найти или создать связку внешнего чата (Telegram ↔ OpenLines) для данного портала.
    """
    chat, created = ExternalChat.objects.get_or_create(
        portal=portal,
        telegram_chat_id=str(telegram_chat_id),
        defaults={
            'telegram_chat_type': telegram_chat_type,
            'telegram_chat_title': telegram_chat_title[:255],
        },
    )
    changed = False
    if chat.telegram_chat_type != telegram_chat_type:
        chat.telegram_chat_type = telegram_chat_type
        changed = True
    title = (telegram_chat_title or str(telegram_chat_id))[:255]
    if chat.telegram_chat_title != title:
        chat.telegram_chat_title = title
        changed = True
    if changed:
        chat.save(update_fields=['telegram_chat_type', 'telegram_chat_title', 'updated_at'])
    return chat


def send_telegram_message_to_openline(
    telegram_chat_id: str,
    telegram_chat_type: str,
    telegram_chat_title: str,
    text: str,
    external_user_id: str | None = None,
    external_user_name: str | None = None,
) -> None:
    """
    Отправить сообщение из Telegram в Открытую линию через imconnector.send.messages.
    Для каждого чата Telegram создаётся/используется один ExternalChat и одна сессия OpenLines.
    """
    if not text:
        return

    portal = get_default_portal()
    connector_id, line_id = _get_openline_connector_and_line()

    external_chat = get_or_create_external_chat_for_telegram(
        portal=portal,
        telegram_chat_id=str(telegram_chat_id),
        telegram_chat_type=telegram_chat_type,
        telegram_chat_title=telegram_chat_title,
    )

    # Bitrix ожидает LINE числом для imconnector
    line_param = int(line_id) if str(line_id).strip().isdigit() else line_id

    # Активируем коннектор для линии, если ещё не активен (без этого send.messages может вернуть "линия неактивна")
    try:
        call_bitrix_rest('imconnector.activate', {
            'CONNECTOR': connector_id,
            'LINE': line_param,
            'ACTIVE': 1,
        })
    except BitrixAuthError as e:
        logger.debug('imconnector.activate (игнор, продолжаем send): %s', e)

    user_id = str(external_user_id or telegram_chat_id)
    user_name = (external_user_name or telegram_chat_title or str(telegram_chat_id))[:25]
    message_id = f'{telegram_chat_id}:{int(time.time() * 1000)}'
    timestamp = int(time.time())

    payload = {
        'CONNECTOR': connector_id,
        'LINE': line_param,
        'MESSAGES': [
            {
                'user': {
                    'id': user_id,
                    'name': user_name,
                    'last_name': '',
                },
                'message': {
                    'id': message_id,
                    'date': timestamp,
                    'text': text,
                },
                'chat': {
                    'id': str(telegram_chat_id),
                    'name': telegram_chat_title or str(telegram_chat_id),
                },
            }
        ],
    }

    result = call_bitrix_rest('imconnector.send.messages', payload)
    try:
        # Bitrix обычно возвращает {"result": {"SUCCESS": true, "DATA": {"RESULT": [...]}}}
        result_block = result.get('result') if isinstance(result.get('result'), dict) else result
        items = (result_block.get('DATA') or {}).get('RESULT') or []
        if items:
            session = items[0].get('session') or {}
            chat_id = session.get('CHAT_ID')
            session_id = session.get('ID')
            logger.info(
                'OpenLines: Bitrix принял сообщение (tg_chat=%s) → session_id=%s chat_id=%s (connector=%s line=%s)',
                telegram_chat_id,
                session_id,
                chat_id,
                connector_id,
                line_param,
            )
            changed = False
            if chat_id and str(chat_id) != external_chat.bitrix_chat_id:
                external_chat.bitrix_chat_id = str(chat_id)
                changed = True
            if session_id and str(session_id) != external_chat.bitrix_dialog_id:
                external_chat.bitrix_dialog_id = str(session_id)
                changed = True
            if changed:
                external_chat.save(update_fields=['bitrix_chat_id', 'bitrix_dialog_id', 'updated_at'])
            # Обновляем имя чата в Bitrix, чтобы в ОЛ отображалось актуальное название группы/чата.
            try:
                call_bitrix_rest(
                    'imconnector.chat.name.set',
                    {
                        'CONNECTOR': connector_id,
                        'LINE': line_param,
                        'CHAT_ID': str(telegram_chat_id),
                        'NAME': (telegram_chat_title or str(telegram_chat_id))[:255],
                    },
                )
            except Exception as exc:
                logger.debug('imconnector.chat.name.set failed for %s: %s', telegram_chat_id, exc)
        else:
            logger.info(
                'OpenLines: сообщение отправлено в Bitrix (tg_chat=%s, connector=%s, line=%s), но session не вернулся в ответе: %s',
                telegram_chat_id,
                connector_id,
                line_param,
                result,
            )
    except Exception as exc:
        logger.warning('Не удалось обновить ExternalChat по результату imconnector.send.messages: %s', exc)


def send_max_message_to_openline(
    max_chat_id: str,
    max_chat_type: str,
    max_chat_title: str,
    text: str,
    max_sender_id: str | None = None,
    max_sender_name: str | None = None,
) -> None:
    """
    Отправить сообщение из MAX в Открытую линию.
    Для ручной работы операторов разделяем потоки:
    - max:g:<group_chat_id>:u:<sender_id> — групповые чаты с явным sender_id,
    - max:dm:<user_id> — личные сообщения боту.
    """
    normalized_chat_type = (max_chat_type or '').strip().lower()
    normalized_chat_id = str(max_chat_id or '').strip()
    normalized_sender_id = str(max_sender_id or '').strip()

    is_group_chat = (
        normalized_chat_type in ('chat', 'group', 'supergroup', 'channel')
        or normalized_chat_id.startswith('-')
    )
    if is_group_chat:
        route_kind = 'group'
        if normalized_sender_id:
            route_id = normalized_chat_id
            external_chat_id = f'max:g:{route_id}:u:{normalized_sender_id}'
        else:
            # Fallback для редких payload без sender_id.
            route_id = normalized_chat_id
            external_chat_id = f'max:group:{route_id}'
    else:
        route_kind = 'dm'
        route_id = normalized_sender_id or normalized_chat_id
        external_chat_id = f'max:dm:{route_id}'

    if not route_id:
        logger.warning('MAX -> OpenLines skipped: empty route_id for chat_id=%s type=%s', max_chat_id, max_chat_type)
        return

    logger.info(
        'MAX -> OpenLines prepare: max_chat_id=%s external_chat_id=%s route=%s chat_type=%s title=%s text_len=%s text_preview=%s',
        max_chat_id,
        external_chat_id,
        route_kind,
        max_chat_type,
        max_chat_title,
        len(text or ''),
        (text or '')[:300],
    )
    send_telegram_message_to_openline(
        telegram_chat_id=external_chat_id,
        telegram_chat_type=max_chat_type or 'private',
        telegram_chat_title=max_chat_title or external_chat_id,
        text=text,
        external_user_id=(normalized_sender_id or route_id),
        external_user_name=max_sender_name,
    )
    logger.info('MAX -> OpenLines sent: external_chat_id=%s route=%s', external_chat_id, route_kind)


def send_status_delivery(connector_id: str, line_id: int | str, messages: list[dict]) -> None:
    """
    Подтвердить доставку сообщений (для Bitrix, чтобы не было "сообщение не доставлено").
    messages: список элементов в формате события OnImConnectorMessageAdd:
      { "im": {"chat_id": ..., "message_id": ...}, "chat": {"id": "..."} }
    """
    if not messages:
        return
    line_param = int(line_id) if str(line_id).strip().isdigit() else line_id
    payload: list[dict] = []
    for item in messages:
        im = item.get('im') or {}
        chat = item.get('chat') or {}
        im_chat_id = im.get('chat_id')
        im_message_id = im.get('message_id')
        external_chat_id = chat.get('id')
        if not im_chat_id or not im_message_id or not external_chat_id:
            continue
        payload.append(
            {
                'im': {
                    'chat_id': im_chat_id,
                    'message_id': im_message_id,
                },
                'message': {
                    'id': [im_message_id],
                },
                'chat': {
                    'id': str(external_chat_id),
                },
            }
        )
    if not payload:
        return
    call_bitrix_rest(
        'imconnector.send.status.delivery',
        {
            'CONNECTOR': connector_id,
            'LINE': line_param,
            'MESSAGES': payload,
        },
    )

