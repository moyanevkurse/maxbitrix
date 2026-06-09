"""
Отправка сообщений в Telegram: текст и опционально медиа (photo/video/document).
"""
import os
import requests
from django.conf import settings

from mailings.services.max import (
    MaxApiError,
    send_media_message as max_send_media_message,
    send_message as max_send_message,
)


def get_bot_token():
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None) or os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError('TELEGRAM_BOT_TOKEN не задан в настройках или .env')
    return token


def send_message(chat_id: str, text: str, parse_mode: str = 'Markdown') -> dict:
    """Отправить только текст (sendMessage)."""
    token = get_bot_token()
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode,
    }
    r = requests.post(url, json=payload, timeout=30)
    data = r.json()
    if not data.get('ok'):
        return {'ok': False, 'error': data.get('description', r.text)}
    return data


def send_photo(chat_id: str, photo_path: str, caption: str = '', parse_mode: str = 'Markdown') -> dict:
    """Отправить фото с подписью (sendPhoto)."""
    token = get_bot_token()
    url = f'https://api.telegram.org/bot{token}/sendPhoto'
    with open(photo_path, 'rb') as f:
        files = {'photo': f}
        data = {'chat_id': chat_id, 'parse_mode': parse_mode}
        if caption:
            data['caption'] = caption[:1024]
        r = requests.post(url, data=data, files=files, timeout=60)
    result = r.json()
    if not result.get('ok'):
        return {'ok': False, 'error': result.get('description', r.text)}
    return result


def send_video(chat_id: str, video_path: str, caption: str = '', parse_mode: str = 'Markdown') -> dict:
    """Отправить видео с подписью (sendVideo)."""
    token = get_bot_token()
    url = f'https://api.telegram.org/bot{token}/sendVideo'
    with open(video_path, 'rb') as f:
        files = {'video': f}
        data = {'chat_id': chat_id, 'parse_mode': parse_mode}
        if caption:
            data['caption'] = caption[:1024]
        r = requests.post(url, data=data, files=files, timeout=120)
    result = r.json()
    if not result.get('ok'):
        return {'ok': False, 'error': result.get('description', r.text)}
    return result


def send_document(chat_id: str, document_path: str, caption: str = '', parse_mode: str = 'Markdown') -> dict:
    """Отправить документ с подписью (sendDocument)."""
    token = get_bot_token()
    url = f'https://api.telegram.org/bot{token}/sendDocument'
    with open(document_path, 'rb') as f:
        files = {'document': f}
        data = {'chat_id': chat_id, 'parse_mode': parse_mode}
        if caption:
            data['caption'] = caption[:1024]
        r = requests.post(url, data=data, files=files, timeout=120)
    result = r.json()
    if not result.get('ok'):
        return {'ok': False, 'error': result.get('description', r.text)}
    return result


# Расширения для автоопределения: фото → sendPhoto, видео → sendVideo, остальное → sendDocument
PHOTO_EXTENSIONS = frozenset(['.jpg', '.jpeg', '.png', '.gif', '.webp'])
VIDEO_EXTENSIONS = frozenset(['.mp4', '.mov', '.avi', '.webm', '.mkv', '.mpeg', '.mpg'])


def _media_send_type(filename: str) -> str:
    """По имени файла: 'photo', 'video' или 'document'."""
    if not filename:
        return 'document'
    ext = os.path.splitext(filename)[1].lower()
    if ext in PHOTO_EXTENSIONS:
        return 'photo'
    if ext in VIDEO_EXTENSIONS:
        return 'video'
    return 'document'


def _template_attachment_paths(template, media_root: str) -> tuple[list[tuple[str, str]], str]:
    """
    Собрать пути к вложениям шаблона.
    Возвращает ([(name, path), ...], error_message).
    """
    files: list[tuple[str, str]] = []

    # Старое одиночное поле оставляем для обратной совместимости.
    if getattr(template, 'media_file', None):
        path = os.path.join(media_root, template.media_file.name)
        if not os.path.isfile(path):
            return [], f'Файл не найден: {path}'
        files.append((template.media_file.name, path))

    # Новые множественные вложения.
    for attachment in getattr(template, 'attachments', []).all():
        path = os.path.join(media_root, attachment.file.name)
        if not os.path.isfile(path):
            return [], f'Файл не найден: {path}'
        files.append((attachment.file.name, path))

    return files, ''


def send_template_to_chat_id(template, chat_id: str, media_root: str) -> tuple[bool, str]:
    """
    Отправить шаблон в один чат. Возвращает (success, error_message).
    Тип отправки файла (фото/видео/документ) определяется по расширению.
    """
    chat_id = str(chat_id)
    text = template.text or ''
    is_max_chat = str(chat_id).startswith('max:')

    files, files_error = _template_attachment_paths(template, media_root)
    if files_error:
        return False, files_error

    if is_max_chat:
        max_chat_id = str(chat_id).split(':', 1)[1]
        try:
            # Единое поведение для MAX/Telegram: сначала текст, затем каждый файл отдельным сообщением.
            if text:
                result = max_send_message(max_chat_id, text)
                if not result.get('ok'):
                    return False, result.get('error', 'Неизвестная ошибка MAX')

            for filename, path in files:
                send_type = _media_send_type(filename)
                result = max_send_media_message(max_chat_id, path, send_type=send_type, text='')
                if not result.get('ok'):
                    return False, result.get('error', 'Неизвестная ошибка MAX')
        except MaxApiError as exc:
            return False, str(exc)
        return True, ''

    if text:
        result = send_message(chat_id, text)
        if not result.get('ok'):
            return False, result.get('error', 'Неизвестная ошибка')

    for filename, path in files:
        send_type = _media_send_type(filename)
        if send_type == 'photo':
            result = send_photo(chat_id, path, caption='')
        elif send_type == 'video':
            result = send_video(chat_id, path, caption='')
        else:
            result = send_document(chat_id, path, caption='')
        if not result.get('ok'):
            return False, result.get('error', 'Неизвестная ошибка')

    return True, ''


def send_template_to_chat(template, partner, media_root: str) -> tuple[bool, str]:
    """Совместимость со старым API: отправка через объект Partner."""
    return send_template_to_chat_id(template=template, chat_id=partner.chat_id, media_root=media_root)
