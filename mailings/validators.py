from django.core.exceptions import ValidationError
from django.conf import settings


def validate_message_length(text, has_media=False):
    """Проверка длины текста: 4096 для сообщения, 1024 для caption."""
    if not text or not text.strip():
        return
    max_len = (
        getattr(settings, 'TELEGRAM_CAPTION_MAX_LENGTH', 1024)
        if has_media
        else getattr(settings, 'TELEGRAM_MESSAGE_MAX_LENGTH', 4096)
    )
    if len(text) > max_len:
        raise ValidationError(
            'Длина текста не должна превышать %(max)s символов (с медиа — подпись до %(caption)s). Сейчас: %(current)s.',
            code='max_length',
            params={
                'max': 4096,
                'caption': 1024,
                'current': len(text),
            },
        )
