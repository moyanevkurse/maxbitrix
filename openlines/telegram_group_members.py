"""Подбор Telegram-участников для автоматического создания групп из Bitrix."""
from __future__ import annotations

import re

from django.conf import settings


def _text(value) -> str:
    if isinstance(value, list):
        for item in value:
            result = _text(item)
            if result:
                return result
        return ''
    if isinstance(value, dict):
        return _text(value.get('VALUE') or value.get('value') or value.get('USERNAME') or value.get('username'))
    return str(value or '').strip()


def _normalise(value) -> str:
    text = _text(value)
    if not text:
        return ''
    compact_phone = re.sub(r'[\s()\-]', '', text)
    if re.fullmatch(r'\+[1-9]\d{6,14}', compact_phone):
        return compact_phone

    if text.startswith('tg:'):
        text = text[3:].strip()
    if text.startswith('@') or text.isdigit():
        return text
    return f'@{text}'


def _append(result: list[str], value) -> None:
    reference = _normalise(value)
    if reference and reference not in result:
        result.append(reference)


def _contact_user_ref(contact: dict) -> str:
    username_field = str(getattr(settings, 'BITRIX_UF_TELEGRAM_USERNAME', '') or '').strip()
    if username_field:
        value = _normalise(contact.get(username_field))
        if value:
            return value
    # Обычное поле телефона контакта Bitrix: используем его только как fallback,
    # когда Telegram username не указан.
    phone = _normalise(contact.get('PHONE'))
    if phone:
        return phone
    # Совместимость с уже заведённым полем Telegram ID: для личного чата
    # положительный chat_id равен user_id. MTProto сможет использовать его,
    # если пользователь уже известен служебному аккаунту.
    return _normalise(contact.get(getattr(settings, 'BITRIX_UF_TELEGRAM_ID', 'UF_CRM_1770617684')))


def _bitrix_user_ref(bitrix_user_id: str) -> str:
    """Ответственный Bitrix → Telegram username/phone по цепочке MAX."""
    if not str(bitrix_user_id or '').strip().isdigit():
        return ''

    user_id = str(bitrix_user_id).strip()
    try:
        from .models import TelegramGroupStaffMember

        staff = (
            TelegramGroupStaffMember.objects.filter(is_active=True, bitrix_user_id=user_id)
            .order_by('sort_order', 'id')
            .first()
        )
        if staff:
            reference = _normalise(staff.telegram_username or staff.telegram_user_id)
            if reference:
                return reference
    except Exception:  # noqa: BLE001
        pass

    from mailings.services.bitrix import find_employee_contact_by_bitrix_user_id, get_user

    employee_contact = find_employee_contact_by_bitrix_user_id(user_id) or {}
    reference = _contact_user_ref(employee_contact)
    if reference:
        return reference

    user = get_user(user_id) or {}
    username_field = str(getattr(settings, 'BITRIX_UF_USER_TELEGRAM_USERNAME', '') or '').strip()
    if username_field:
        reference = _normalise(user.get(username_field))
        if reference:
            return reference
    for phone_field in ('PERSONAL_MOBILE', 'WORK_PHONE', 'PERSONAL_PHONE'):
        reference = _normalise(user.get(phone_field))
        if reference:
            return reference
    return ''


def _staff_refs(kind: str) -> list[str]:
    from .models import TelegramGroupStaffMember

    filter_key = 'add_for_contact' if kind == 'contact' else 'add_for_company'
    result: list[str] = []
    for staff in TelegramGroupStaffMember.objects.filter(is_active=True, **{filter_key: True}):
        _append(result, staff.telegram_username or staff.telegram_user_id)
    return result


def resolve_telegram_group_user_refs(entity_type: str, entity_id: str) -> list[str]:
    """Участники: CRM-контакт + ответственный Bitrix + TG-список сотрудников."""
    kind = str(entity_type or '').strip().lower()
    eid = str(entity_id or '').strip()
    if kind not in {'contact', 'company'} or not eid.isdigit():
        return []

    from mailings.services.bitrix import call_bitrix, get_contact

    result: list[str] = []
    if kind == 'contact':
        contact = get_contact(eid) or {}
        _append(result, _contact_user_ref(contact))
        _append(result, _bitrix_user_ref(str(contact.get('ASSIGNED_BY_ID') or '')))
    else:
        # Как MAX: для компании используются только PHONE с VALUE_TYPE=MAILING.
        # Далее Telethon резолвит номер в Telegram; username остаётся предпочтительным
        # механизмом для контактов, где он заполнен.
        from .entity_phones import resolve_company_group_phones

        for phone in resolve_company_group_phones(eid):
            _append(result, phone)
        payload = call_bitrix('crm.company.get', {'id': eid})
        company = payload.get('result') or {}
        _append(result, _bitrix_user_ref(str(company.get('ASSIGNED_BY_ID') or '')))

    for ref in _staff_refs(kind):
        _append(result, ref)
    return result

