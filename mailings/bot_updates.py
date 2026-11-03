"""
Обработка входящих обновлений Telegram (python-telegram-bot):
при добавлении бота в группу/канал или при сообщении в личку
создаётся запись «Партнёр» в админке и при наличии BITRIX_WEBHOOK_URL —
контакт в Битрикс24 (поиск по полю «Telegram ID» или создание с привязкой).
Long polling — вебхук не нужен.
"""
import logging

from asgiref.sync import sync_to_async
from telegram import Update, Chat, ChatMember
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    ChatMemberHandler,
    filters,
)

from .models import Partner

logger = logging.getLogger(__name__)

CHAT_TYPE_DISPLAY = {
    Partner.TYPE_GROUP: 'Группа',
    Partner.TYPE_CHANNEL: 'Канал',
    Partner.TYPE_PRIVATE: 'Личное сообщение',
}

TELEGRAM_TO_OUR_TYPE = {
    Chat.PRIVATE: Partner.TYPE_PRIVATE,
    Chat.GROUP: Partner.TYPE_GROUP,
    Chat.SUPERGROUP: Partner.TYPE_GROUP,
    Chat.CHANNEL: Partner.TYPE_CHANNEL,
}


def _ensure_partner_sync(chat_id: str, chat_type: str, title: str) -> None:
    """Создать или обновить партнёра (чат в Telegram)."""
    partner, created = Partner.objects.update_or_create(
        chat_id=chat_id,
        defaults={
            'chat_type': chat_type,
            'title': title or chat_id,
            'is_active': True,
        },
    )
    if created:
        logger.info('Создан партнёр: %s (%s)', title or chat_id, chat_id)
    else:
        if partner.title != (title or chat_id):
            partner.title = title or chat_id
            partner.save(update_fields=['title'])


def _sync_partner_with_bitrix(chat_id: str, chat_type: str, title: str) -> None:
    """
    Найти контакт в Битрикс по названию (NAME) или создать новый.
    Найденному контакту проставляем Telegram ID (chat_id), чтобы рассылка по стадии сделки работала.
    Работает и для групп/каналов, и для лички.
    """
    from django.conf import settings
    if not getattr(settings, 'BITRIX_WEBHOOK_URL', '').strip():
        return
    from mailings.services.bitrix import (
        find_contact_by_name,
        add_contact,
        update_contact,
        _get_uf_telegram_id,
    )
    type_label = CHAT_TYPE_DISPLAY.get(chat_type, chat_type)
    comments = f"Telegram: {chat_id} ({type_label})\nНазвание: {title or chat_id}"
    name = (title or chat_id)[:255]
    contact = find_contact_by_name(name)
    uf_telegram = _get_uf_telegram_id()
    if contact:
        update_contact(
            contact['ID'],
            {
                'NAME': name,
                'COMMENTS': comments,
                uf_telegram: chat_id,
            },
        )
        logger.info('Битрикс: обновлён контакт ID=%s по названию "%s", проставлен Telegram ID', contact['ID'], name)
    else:
        add_contact(name=name, comments=comments, telegram_id=chat_id)
        logger.info('Битрикс: создан контакт для чата %s (%s)', chat_id, name)


def _sync_group_with_bitrix_by_messenger_number(chat_id: str, title: str, update_type: str) -> None:
    """Связать ручную TG-группу по номеру в title, как MAX-группу."""
    try:
        from openlines.telegram_group_bitrix_sync import sync_telegram_group_to_bitrix_by_title

        result = sync_telegram_group_to_bitrix_by_title(
            chat_id=chat_id,
            title=title,
            update_type=update_type,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            'Telegram group Bitrix sync failed: chat_id=%s title=%r error=%s',
            chat_id, title, exc, exc_info=True,
        )
        return
    if result.get('ok'):
        logger.info(
            'Telegram group Bitrix sync: chat_id=%s group_number=%s contacts=%s companies=%s',
            chat_id, result.get('group_number'), result.get('contact_matches'), result.get('company_matches'),
        )
    else:
        logger.info(
            'Telegram group Bitrix sync skipped: chat_id=%s title=%r reason=%s',
            chat_id, title, result.get('error'),
        )


async def _ensure_partner(
    chat_id: str,
    chat_type: str,
    title: str,
    *,
    group_sync_enabled: bool = True,
) -> None:
    await sync_to_async(_ensure_partner_sync)(chat_id, chat_type, title)
    if chat_type == Partner.TYPE_GROUP:
        if group_sync_enabled:
            await sync_to_async(_sync_group_with_bitrix_by_messenger_number)(chat_id, title, 'bot_added_or_message')
        return
    await sync_to_async(_sync_partner_with_bitrix)(chat_id, chat_type, title)


async def _send_to_openlines(chat_id: str, chat_type: str, title: str, text: str) -> None:
    """
    Отправить текст сообщения в Открытую линию, если настроен коннектор.
    Ошибки логируем, бот не падает.
    """
    if not text:
        return
    try:
        from openlines.services import send_telegram_message_to_openline
    except Exception as exc:
        logger.debug('OpenLines bridge not available: %s', exc)
        return
    try:
        await sync_to_async(send_telegram_message_to_openline)(
            telegram_chat_id=chat_id,
            telegram_chat_type=chat_type,
            telegram_chat_title=title,
            text=text,
        )
    except Exception as exc:
        logger.warning('OpenLines: не удалось отправить сообщение в Bitrix: %s', exc, exc_info=True)


def _chat_to_partner_data(chat: Chat, from_user_first_name: str | None = None) -> tuple[str, str, str] | None:
    """(chat_id, our_chat_type, title) или None."""
    our_type = TELEGRAM_TO_OUR_TYPE.get(chat.type)
    if not our_type:
        return None
    chat_id = str(chat.id)
    title = chat.title or chat.username or ''
    if chat.type == Chat.PRIVATE and from_user_first_name:
        title = from_user_first_name or title or chat_id
    title = (title or chat_id)[:255]
    return chat_id, our_type, title


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Любое сообщение — создаём/обновляем партнёра и отправляем его текст в Открытую линию.
    """
    chat = update.effective_chat
    if not chat:
        return
    from_name = (update.effective_user and update.effective_user.first_name) or None
    data = _chat_to_partner_data(chat, from_user_first_name=from_name)
    if not data:
        return
    chat_id, chat_type, title = data
    await _ensure_partner(chat_id, chat_type, title)

    message = update.effective_message
    if not message:
        return
    text = message.text or message.caption or ''
    await _send_to_openlines(chat_id, chat_type, title, text)


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Бота добавили/удалили в чат — создаём/обновляем партнёра."""
    chat = update.my_chat_member and update.my_chat_member.chat
    if not chat:
        return
    data = _chat_to_partner_data(chat)
    if not data:
        return
    chat_id, chat_type, title = data
    new_status = update.my_chat_member.new_chat_member.status
    bot_is_in_group = new_status in {
        ChatMember.MEMBER,
        ChatMember.ADMINISTRATOR,
        ChatMember.RESTRICTED,
    }
    await _ensure_partner(
        chat_id, chat_type, title,
        group_sync_enabled=chat_type != Partner.TYPE_GROUP or bot_is_in_group,

    )

def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.ALL, on_message))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    return app


def run_polling(token: str, timeout: int = 30) -> None:
    """Запуск long polling (вебхук не нужен, работает на локалке)."""
    app = build_application(token)
    app.run_polling(
        timeout=timeout,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )
