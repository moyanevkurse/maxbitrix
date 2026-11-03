"""Явная привязка автоматически созданной Telegram-группы к Bitrix."""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


def bind_telegram_group_to_bitrix(*, entity_type: str, entity_id: str, chat_id: str, title: str) -> dict:
    """Сохраняет связь локально и в настроенное UF Bitrix, не полагаясь на title."""
    kind = str(entity_type or '').strip().lower()
    eid = str(entity_id or '').strip()
    normalized_chat_id = str(chat_id or '').strip()
    if kind not in {'contact', 'company'} or not eid.isdigit() or not normalized_chat_id:
        return {'ok': False, 'error': 'invalid entity or chat id'}

    from mailings.models import BitrixCompanySnapshot, BitrixContactSnapshot, Partner
    from mailings.services.bitrix import update_company, update_contact

    result: dict = {'ok': True, 'entity_type': kind, 'entity_id': eid, 'chat_id': normalized_chat_id}
    with transaction.atomic():
        Partner.objects.update_or_create(
            chat_id=normalized_chat_id,
            defaults={
                'chat_type': Partner.TYPE_GROUP,
                'title': (title or normalized_chat_id)[:255],
                'is_active': True,
            },
        )

        if kind == 'contact':
            snapshot, _ = BitrixContactSnapshot.objects.get_or_create(
                bitrix_contact_id=int(eid),
                defaults={'full_name': ''},
            )
            snapshot.telegram_group_id = normalized_chat_id
            snapshot.save(update_fields=['telegram_group_id', 'synced_at'])
            uf_field = str(getattr(settings, 'BITRIX_UF_TELEGRAM_GROUP_ID', '') or '').strip()
            if uf_field:
                updated, error = update_contact(eid, {uf_field: normalized_chat_id})
                result['bitrix_updated'] = updated
                if not updated:
                    result['bitrix_error'] = error
            else:
                result['bitrix_updated'] = False
                result['bitrix_skipped'] = 'BITRIX_UF_TELEGRAM_GROUP_ID is empty'
            return result

        snapshot, _ = BitrixCompanySnapshot.objects.get_or_create(
            bitrix_company_id=int(eid),
            defaults={'title': ''},
        )
        snapshot.telegram_group_id = normalized_chat_id
        snapshot.save(update_fields=['telegram_group_id', 'synced_at'])
        uf_field = str(getattr(settings, 'BITRIX_UF_COMPANY_TELEGRAM_GROUP_ID', '') or '').strip()
        if uf_field:
            updated, error = update_company(eid, {uf_field: normalized_chat_id})
            result['bitrix_updated'] = updated
            if not updated:
                result['bitrix_error'] = error
        else:
            result['bitrix_updated'] = False
            result['bitrix_skipped'] = 'BITRIX_UF_COMPANY_TELEGRAM_GROUP_ID is empty'
    return result


def sync_telegram_group_to_bitrix_by_title(*, chat_id: str, title: str, update_type: str = '') -> dict:
    """
    Ручной сценарий: бот добавлен в существующую TG-группу.

    Повторяет безопасный алгоритм MAX: номер берётся из начала title до '/',
    затем ищется в контактах и компаниях по полю «Номер чата в мессенджере».
    Не связывает ничего при отсутствии или неоднозначности совпадений.
    """
    normalized_chat_id = str(chat_id or '').strip()
    resolved_title = str(title or '').strip()
    if not normalized_chat_id:
        return {'ok': False, 'error': 'chat_id is empty'}

    # Функции поиска используют те же Bitrix-поля и нормализацию номера, что MAX.
    from .max_group_bitrix_sync import (
        extract_group_number_from_title,
        find_companies_by_messenger_chat_number,
        find_contacts_by_messenger_chat_number,
    )

    group_number = extract_group_number_from_title(resolved_title)
    if not group_number:
        return {
            'ok': False,
            'error': 'group_number not found in title',
            'chat_id': normalized_chat_id,
            'title': resolved_title,
            'update_type': update_type,
        }

    contact_matches = find_contacts_by_messenger_chat_number(group_number)
    company_matches = find_companies_by_messenger_chat_number(group_number)
    if not contact_matches and not company_matches:
        return {
            'ok': False,
            'error': 'contact/company not found by messenger_chat_number',
            'chat_id': normalized_chat_id,
            'title': resolved_title,
            'group_number': group_number,
            'update_type': update_type,
        }
    if len(contact_matches) > 1 or len(company_matches) > 1:
        return {
            'ok': False,
            'error': 'multiple contacts or companies with same messenger_chat_number',
            'chat_id': normalized_chat_id,
            'title': resolved_title,
            'group_number': group_number,
            'contact_matches': contact_matches,
            'company_matches': company_matches,
            'update_type': update_type,
        }

    bindings: list[dict] = []
    for match in contact_matches:
        bindings.append(bind_telegram_group_to_bitrix(
            entity_type='contact',
            entity_id=str(match['id']),
            chat_id=normalized_chat_id,
            title=resolved_title,
        ))
    for match in company_matches:
        bindings.append(bind_telegram_group_to_bitrix(
            entity_type='company',
            entity_id=str(match['id']),
            chat_id=normalized_chat_id,
            title=resolved_title,
        ))

    return {
        'ok': all(item.get('ok') for item in bindings),
        'chat_id': normalized_chat_id,
        'title': resolved_title,
        'group_number': group_number,
        'update_type': update_type,
        'contact_matches': contact_matches,
        'company_matches': company_matches,
        'bindings': bindings,
    }
