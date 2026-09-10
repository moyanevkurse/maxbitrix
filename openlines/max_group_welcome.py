"""Сборка и вложения приветственного сообщения в новую MAX-группу."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_WELCOME_MEDIA_FILES = (
    'Гибка профиля.png',
    'Decoplume каталог 2026.pdf',
    'LACONISTIQ & DECOPLUME v3.4 (2026).pdf',
    'Laconistiq каталог 2026.pdf',
)

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.webm'}


def normalize_max_chat_id(chat_id: str) -> str:
    normalized = str(chat_id or '').strip()
    if normalized.startswith('max:'):
        normalized = normalized.split(':', 1)[1].strip()
    return normalized


def fetch_group_invite_link(chat_id: str) -> str:
    from mailings.services.max import MaxApiError, get_chat_invite_link

    normalized = normalize_max_chat_id(chat_id)
    if not normalized:
        return ''
    try:
        return str(get_chat_invite_link(normalized) or '').strip()
    except MaxApiError as exc:
        logger.warning('MAX welcome: invite link fetch failed chat_id=%s error=%s', normalized, exc)
        return ''


def _media_send_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return 'photo'
    if suffix in VIDEO_EXTENSIONS:
        return 'video'
    return 'document'


def resolve_welcome_media_files() -> list[tuple[Path, str]]:
    """
    Возвращает [(абсолютный путь, send_type), ...].

    Приоритет:
    1) вложения из MaxGroupWelcomeConfig в админке
    2) fallback на settings.MAX_GROUP_WELCOME_MEDIA_FILES / дефолтные имена в MEDIA_ROOT
    """
    if not bool(getattr(settings, 'MAX_GROUP_WELCOME_MEDIA_ENABLED', True)):
        return []

    db_files = _resolve_welcome_media_from_db()
    if db_files:
        return db_files
    return _resolve_welcome_media_from_settings()


def _resolve_welcome_media_from_db() -> list[tuple[Path, str]]:
    try:
        from .models import MaxGroupWelcomeConfig

        cfg = (
            MaxGroupWelcomeConfig.objects.filter(is_active=True)
            .prefetch_related('attachments')
            .order_by('-updated_at', '-id')
            .first()
        )
        if not cfg:
            return []

        resolved: list[tuple[Path, str]] = []
        for attachment in cfg.attachments.all():
            if not attachment.file:
                continue
            try:
                path = Path(attachment.file.path).resolve()
            except Exception as exc:  # noqa: BLE001
                logger.warning('MAX welcome media: cannot resolve attachment id=%s: %s', attachment.pk, exc)
                continue
            if not path.is_file():
                logger.warning('MAX welcome media: attachment file missing: %s', path)
                continue
            resolved.append((path, _media_send_type(path)))
        return resolved
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX welcome media: DB attachments unavailable: %s', exc)
        return []


def _resolve_welcome_media_from_settings() -> list[tuple[Path, str]]:
    raw = str(getattr(settings, 'MAX_GROUP_WELCOME_MEDIA_FILES', '') or '').strip()
    if not raw:
        names = list(DEFAULT_WELCOME_MEDIA_FILES)
    else:
        names = [part.strip() for part in raw.replace('\n', '|').split('|') if part.strip()]

    media_root = Path(str(getattr(settings, 'MEDIA_ROOT', '') or '')).resolve()
    if not media_root.is_dir():
        logger.warning('MAX welcome media: MEDIA_ROOT not found: %s', media_root)
        return []

    resolved: list[tuple[Path, str]] = []
    for name in names:
        path = (media_root / name).resolve()
        if not path.is_file():
            logger.warning('MAX welcome media: file not found: %s', path)
            continue
        try:
            path.relative_to(media_root)
        except ValueError:
            logger.warning('MAX welcome media: path outside MEDIA_ROOT skipped: %s', path)
            continue
        resolved.append((path, _media_send_type(path)))
    return resolved


def build_welcome_message_text(*, welcome_text: str, title: str, chat_id: str, invite_link: str) -> str:
    normalized_chat_id = normalize_max_chat_id(chat_id)
    format_kwargs = {
        'title': title or '',
        'chat_id': normalized_chat_id,
        'link': invite_link or '',
    }
    try:
        text = welcome_text.format(**format_kwargs)
    except Exception:  # noqa: BLE001
        text = welcome_text

    include_link = bool(getattr(settings, 'MAX_GROUP_WELCOME_INCLUDE_INVITE_LINK', True))
    if include_link and invite_link and invite_link not in text:
        suffix = str(getattr(settings, 'MAX_GROUP_WELCOME_INVITE_SUFFIX', '') or '')
        # По умолчанию — продолжение того же сообщения: пробел и ссылка.
        if not suffix.strip():
            text = f'{text.rstrip()} {invite_link}'.strip() if text.strip() else invite_link
        else:
            try:
                text = text.rstrip() + suffix.format(**format_kwargs)
            except Exception:  # noqa: BLE001
                text = f'{text.rstrip()}{suffix}'.replace('{link}', invite_link)

    return text


def welcome_will_include_invite_link() -> bool:
    welcome_text = str(get_max_group_welcome_text() or '').strip()
    if not welcome_text:
        return False
    return bool(getattr(settings, 'MAX_GROUP_WELCOME_INCLUDE_INVITE_LINK', True))


def get_max_group_welcome_text() -> str:
    """
    Возвращает текст приветствия для новых MAX-групп.

    Источник приоритетом:
    1) запись `MaxGroupWelcomeConfig` в БД (если есть и is_active=True)
    2) fallback на `settings.MAX_GROUP_WELCOME_TEXT`
    """

    try:
        from .models import MaxGroupWelcomeConfig

        cfg = (
            MaxGroupWelcomeConfig.objects.filter(is_active=True)
            .order_by('-updated_at', '-id')
            .first()
        )
        if cfg and str(cfg.welcome_text or '').strip():
            return str(cfg.welcome_text or '')
    except Exception:  # noqa: BLE001
        # На случай миграций/ошибок модели — безопасный fallback на settings.
        pass

    return str(getattr(settings, 'MAX_GROUP_WELCOME_TEXT', '') or '')
