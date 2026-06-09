"""
Адрес контакта/компании из реквизитов Bitrix (crm.requisite.list + crm.address.list).
В карточке «Фактический адрес» часто не попадает в crm.contact.get → ADDRESS.
"""
from __future__ import annotations

import logging
from typing import Any

from mailings.services.bitrix import call_bitrix

logger = logging.getLogger(__name__)

# Типы сущностей CRM (crm.enum.ownertype)
ENTITY_TYPE_CONTACT = 3
ENTITY_TYPE_COMPANY = 4
# Адрес привязан к реквизиту
ENTITY_TYPE_REQUISITE = 8
# Фактический адрес (см. crm.enum.addresstype)
ADDRESS_TYPE_FACTUAL = 1


def _format_address_record(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ('COUNTRY', 'PROVINCE', 'REGION', 'CITY', 'ADDRESS_1', 'ADDRESS_2', 'POSTAL_CODE'):
        value = str(record.get(key) or '').strip()
        if not value or value in parts:
            continue
        parts.append(value)
    return ', '.join(parts)


def _pick_best_address(records: list[dict[str, Any]]) -> str:
    if not records:
        return ''
    for record in records:
        if str(record.get('TYPE_ID') or '').strip() == str(ADDRESS_TYPE_FACTUAL):
            text = _format_address_record(record)
            if text:
                return text
    for record in records:
        text = _format_address_record(record)
        if text:
            return text
    return ''


def _list_requisite_ids(*, entity_type_id: int, entity_id: int) -> list[int]:
    try:
        payload = call_bitrix(
            'crm.requisite.list',
            {
                'filter': {
                    'ENTITY_TYPE_ID': entity_type_id,
                    'ENTITY_ID': entity_id,
                },
                'select': ['ID'],
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            'Bitrix requisite.list failed: entity_type=%s entity_id=%s error=%s',
            entity_type_id,
            entity_id,
            exc,
        )
        return []

    ids: list[int] = []
    for row in payload.get('result') or []:
        if not isinstance(row, dict):
            continue
        try:
            req_id = int(row.get('ID'))
        except (TypeError, ValueError):
            continue
        if req_id > 0:
            ids.append(req_id)
    return ids


def _list_addresses_for_requisite(requisite_id: int) -> list[dict[str, Any]]:
    try:
        payload = call_bitrix(
            'crm.address.list',
            {
                'filter': {
                    'ENTITY_TYPE_ID': ENTITY_TYPE_REQUISITE,
                    'ENTITY_ID': requisite_id,
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning('Bitrix address.list failed: requisite_id=%s error=%s', requisite_id, exc)
        return []

    result = payload.get('result') or []
    if not isinstance(result, list):
        return []
    return [row for row in result if isinstance(row, dict)]


def fetch_entity_address_text(*, entity_type_id: int, entity_id: int | str) -> str:
    """
    Собирает текст адреса по реквизитам контакта (3) или компании (4).
    """
    try:
        normalized_id = int(entity_id)
    except (TypeError, ValueError):
        return ''
    if normalized_id <= 0:
        return ''

    for requisite_id in _list_requisite_ids(entity_type_id=entity_type_id, entity_id=normalized_id):
        address_text = _pick_best_address(_list_addresses_for_requisite(requisite_id))
        if address_text:
            return address_text
    return ''


def pick_contact_address(contact: dict[str, Any], *, use_requisites: bool = True) -> str:
    """Адрес для snapshot: реквизиты → legacy поля контакта → UF."""
    if use_requisites:
        contact_id = contact.get('ID')
        if contact_id:
            requisite_address = fetch_entity_address_text(
                entity_type_id=ENTITY_TYPE_CONTACT,
                entity_id=contact_id,
            )
            if requisite_address:
                return requisite_address

    legacy_parts: list[str] = []
    for key in ('ADDRESS', 'ADDRESS_CITY', 'ADDRESS_COUNTRY', 'ADDRESS_REGION'):
        value = str(contact.get(key) or '').strip()
        if value and value not in legacy_parts:
            legacy_parts.append(value)
    if legacy_parts:
        return ', '.join(legacy_parts)

    for key in ('UF_CRM_1747678283', 'UF_CRM_1742561008637', 'UF_CRM_680A1B15C9661', 'UF_CRM_REGION'):
        value = str(contact.get(key) or '').strip()
        if value:
            return value
    return ''


def pick_company_address(company: dict[str, Any], *, use_requisites: bool = True) -> str:
    if use_requisites:
        company_id = company.get('ID')
        if company_id:
            requisite_address = fetch_entity_address_text(
                entity_type_id=ENTITY_TYPE_COMPANY,
                entity_id=company_id,
            )
            if requisite_address:
                return requisite_address

    for key in (
        'UF_CRM_1747678283',
        'UF_CRM_1742561008637',
        'UF_CRM_680A1B15C9661',
        'ADDRESS_LEGAL',
        'REG_ADDRESS',
        'ADDRESS',
        'ADDRESS_CITY',
        'REG_ADDRESS_CITY',
    ):
        value = str(company.get(key) or '').strip()
        if value:
            return value
    return ''
