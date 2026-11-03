"""MTProto-клиент служебного Telegram-аккаунта для создания супергрупп без браузера."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class TelegramGroupCreateError(Exception):
    """Невозможно создать или настроить Telegram-группу."""


def _session_path() -> Path:
    raw_path = str(getattr(settings, 'TELEGRAM_GROUP_SESSION_FILE', '') or '').strip()
    if not raw_path:
        raise TelegramGroupCreateError('TELEGRAM_GROUP_SESSION_FILE is not configured')
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _client():
    api_id = int(getattr(settings, 'TELEGRAM_GROUP_API_ID', 0) or 0)
    api_hash = str(getattr(settings, 'TELEGRAM_GROUP_API_HASH', '') or '').strip()
    if api_id <= 0 or not api_hash:
        raise TelegramGroupCreateError('TELEGRAM_GROUP_API_ID and TELEGRAM_GROUP_API_HASH must be configured')

    try:
        # Сервис ниже работает в синхронном Django/Celery-коде. Импорт из
        # telethon.sync оборачивает coroutine-методы в синхронные вызовы.
        from telethon.sync import TelegramClient
    except ImportError as exc:
        raise TelegramGroupCreateError('Telethon is not installed; run pip install -r requirements.txt') from exc

    return TelegramClient(str(_session_path()), api_id, api_hash)

_PHONE_RE = re.compile(r'^\+[1-9]\d{6,14}$')


def _normalise_ref(value) -> str:
    if isinstance(value, dict):
        value = value.get('username') or value.get('phone') or value.get('user_id') or value.get('id') or ''
    text = str(value or '').strip()
    if not text:
        return ''
    compact_phone = re.sub(r'[\s()\-]', '', text)
    if _PHONE_RE.fullmatch(compact_phone):
        return compact_phone
    if text.startswith('tg:'):
        text = text[3:].strip()
    if text.startswith('@'):
        return text
    if text.isdigit():
        return text
    return f'@{text}'


def _resolve_phone_entity(client, phone: str):
    """
    Находит E.164-номер только среди существующих контактов сервисного аккаунта.

    Номер никогда не импортируется: создание группы не меняет адресную книгу
    и локальное имя контакта сервисного Telegram-аккаунта.
    """
    try:
        from telethon import functions

        contacts = client(functions.contacts.GetContactsRequest(hash=0))
        wanted_phone = phone.lstrip('+')
        for known_user in getattr(contacts, 'users', []) or []:
            known_phone = str(getattr(known_user, 'phone', '') or '').lstrip('+')
            if known_phone == wanted_phone:
                return client.get_input_entity(known_user)
        raise TelegramGroupCreateError(
            f'phone {phone} is not an existing contact of the service account; '
            'use @username, a known Telegram ID, or an invite link'
        )
    except TelegramGroupCreateError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise TelegramGroupCreateError(f'cannot resolve Telegram participant by phone {phone}') from exc


def _resolve_entity(client, reference: str):
    if _PHONE_RE.fullmatch(reference):
        return _resolve_phone_entity(client, reference)
    try:
        return client.get_input_entity(reference)
    except Exception as exc:  # noqa: BLE001
        raise TelegramGroupCreateError(
            f'cannot resolve Telegram participant {reference!r}; '
            'use a public @username or an entity known to the service account'
        ) from exc


def _set_group_photo(client, channel, avatar_url: str) -> str:
    if not avatar_url:
        return ''
    try:
        from telethon import functions, types

        response = requests.get(avatar_url, timeout=30)
        response.raise_for_status()
        if len(response.content) > 10 * 1024 * 1024:
            raise TelegramGroupCreateError('group avatar exceeds 10 MB')
        uploaded = client.upload_file(response.content, file_name=Path(urlparse(avatar_url).path).name or 'avatar.jpg')
        client(functions.channels.EditPhotoRequest(
            channel=channel,
            photo=types.InputChatUploadedPhoto(file=uploaded),
        ))
        return avatar_url
    except TelegramGroupCreateError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning('Telegram group avatar setup failed: %s', exc)
        return ''


def _promote_bot(client, channel, bot) -> str:
    if not bool(getattr(settings, 'TELEGRAM_GROUP_BOT_GRANT_ADMIN', True)):
        return 'disabled'
    try:
        from telethon import functions, types

        rights = types.ChatAdminRights(
            change_info=True,
            post_messages=False,
            edit_messages=False,
            delete_messages=False,
            ban_users=False,
            invite_users=True,
            pin_messages=False,
            add_admins=False,
            anonymous=False,
            manage_call=False,
            other=False,
            manage_topics=False,
            post_stories=False,
            edit_stories=False,
            delete_stories=False,
        )
        client(functions.channels.EditAdminRequest(
            channel=channel,
            user_id=bot,
            admin_rights=rights,
            rank='Bot',
        ))
        participant_result = client(functions.channels.GetParticipantRequest(
            channel=channel,
            participant=bot,
        ))
        participant = participant_result.participant
        if isinstance(participant, (types.ChannelParticipantAdmin, types.ChannelParticipantCreator)):
            return 'promoted_verified'
        return f'not_promoted: Telegram returned {type(participant).__name__}'
    except Exception as exc:  # noqa: BLE001
        logger.warning('Telegram group bot promotion failed: %s', exc)
        return f'not_promoted: {exc}'


def create_telegram_group(
    *,
    title: str,
    member_refs: list,
    bot_username: str = '',
    avatar_url: str = '',
) -> dict:
    """
    Создаёт супергруппу от служебного Telegram-аккаунта и добавляет участников.

    В отличие от Bot API это официальный пользовательский MTProto API; браузер
    не запускается. Session-файл должен быть создан отдельной командой входа.
    """
    safe_title = str(title or '').strip()[:255]
    if not safe_title:
        raise TelegramGroupCreateError('group title is empty')
    effective_bot_username = str(bot_username or getattr(settings, 'TELEGRAM_GROUP_BOT_USERNAME', '') or '').strip()
    if not effective_bot_username:
        raise TelegramGroupCreateError('TELEGRAM_GROUP_BOT_USERNAME must be configured')


    try:
        from telethon import functions, types, utils
    except ImportError as exc:
        raise TelegramGroupCreateError('Telethon is not installed; run pip install -r requirements.txt') from exc

    client = _client()
    try:
        client.connect()
        if not client.is_user_authorized():
            raise TelegramGroupCreateError(
                'Telegram service session is not authorized; run the telegram_group_login command once'
            )

        created = client(functions.channels.CreateChannelRequest(
            title=safe_title,
            about='',
            megagroup=True,
            for_import=False,
        ))
        channel = next((chat for chat in created.chats if isinstance(chat, types.Channel)), None)
        if channel is None:
            raise TelegramGroupCreateError('Telegram did not return a created supergroup')
        input_channel = client.get_input_entity(channel)
        chat_id = str(utils.get_peer_id(channel))

        invited: list[str] = []
        invite_errors: list[dict] = []
        seen: set[str] = set()

        normalized_bot = _normalise_ref(effective_bot_username)
        bot_entity = None
        if normalized_bot:
            try:
                bot_entity = _resolve_entity(client, normalized_bot)
                client(functions.channels.InviteToChannelRequest(channel=input_channel, users=[bot_entity]))
                invited.append(normalized_bot)
            except Exception as exc:  # noqa: BLE001
                invite_errors.append({'participant': normalized_bot, 'error': str(exc)})

        for raw_ref in member_refs:
            reference = _normalise_ref(raw_ref)
            if not reference or reference in seen or reference == normalized_bot:
                continue
            seen.add(reference)
            try:
                entity = _resolve_entity(client, reference)
                client(functions.channels.InviteToChannelRequest(channel=input_channel, users=[entity]))
                invited.append(reference)
            except Exception as exc:  # noqa: BLE001
                invite_errors.append({'participant': reference, 'error': str(exc)})

        bot_admin = _promote_bot(client, input_channel, bot_entity) if bot_entity else 'bot_not_added'
        applied_avatar = _set_group_photo(client, input_channel, str(avatar_url or '').strip())

        return {
            'ok': True,
            'chat_id': chat_id,
            'title': safe_title,
            'invited': invited,
            'invite_errors': invite_errors,
            'bot_admin': bot_admin,
            'avatar_url': applied_avatar,
        }
    except TelegramGroupCreateError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise TelegramGroupCreateError(str(exc)) from exc
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
