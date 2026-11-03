"""Приветствие и вложения для автоматически созданных Telegram-групп."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
_VIDEO_EXTENSIONS = {'.mp4', '.mov', '.webm', '.avi', '.mkv'}


def _send_type(path: Path) -> str:
    if path.suffix.lower() in _IMAGE_EXTENSIONS:
        return 'photo'
    if path.suffix.lower() in _VIDEO_EXTENSIONS:
        return 'video'
    return 'document'


def welcome_content() -> tuple[str, list[Path]]:
    try:
        from .models import TelegramGroupWelcomeConfig

        config = (
            TelegramGroupWelcomeConfig.objects.filter(is_active=True)
            .prefetch_related('attachments')
            .order_by('-updated_at', '-id')
            .first()
        )
        if config:
            files: list[Path] = []
            for attachment in config.attachments.all():
                try:
                    path = Path(attachment.file.path).resolve()
                except Exception:  # noqa: BLE001
                    continue
                if path.is_file():
                    files.append(path)
            if (config.welcome_text or '').strip() or files:
                return str(config.welcome_text or ''), files
    except Exception as exc:  # noqa: BLE001
        logger.warning('Telegram welcome config read failed: %s', exc)
    return str(getattr(settings, 'TELEGRAM_GROUP_WELCOME_TEXT', '') or ''), []


def send_telegram_group_welcome(*, chat_id: str, title: str) -> dict:
    from mailings.services.telegram import (
        create_chat_invite_link,
        send_document,
        send_message,
        send_photo,
        send_video,
    )

    text, files = welcome_content()
    invite_link = ''
    if bool(getattr(settings, 'TELEGRAM_GROUP_INVITE_LINK_ENABLED', True)):
        invite = create_chat_invite_link(chat_id)
        if invite.get('ok'):
            invite_link = str((invite.get('result') or {}).get('invite_link') or '')
    if invite_link:
        template = str(getattr(settings, 'TELEGRAM_GROUP_INVITE_LINK_TEXT', '') or '')
        suffix = template.format(link=invite_link, title=title or '', chat_id=chat_id)
        text = f'{text.rstrip()}\n\n{suffix}'.strip()

    sent_files = 0
    errors: list[str] = []
    if text:
        response = send_message(chat_id, text)
        if not response.get('ok'):
            errors.append(str(response.get('error') or 'welcome message failed'))

    for path in files:
        try:
            if path.suffix.lower() in _IMAGE_EXTENSIONS:
                response = send_photo(chat_id, str(path))
            elif path.suffix.lower() in _VIDEO_EXTENSIONS:
                response = send_video(chat_id, str(path))
            else:
                response = send_document(chat_id, str(path))
            if response.get('ok'):
                sent_files += 1
            else:
                errors.append(f'{path.name}: {response.get("error") or "send failed"}')
            time.sleep(0.5)
        except Exception as exc:  # noqa: BLE001
            errors.append(f'{path.name}: {exc}')

    return {'ok': not errors, 'invite_link': invite_link, 'files_sent': sent_files, 'errors': errors}

