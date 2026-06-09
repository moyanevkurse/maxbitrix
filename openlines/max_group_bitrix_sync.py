"""
Связка MAX-группы с контактом и компанией Bitrix по номеру из названия чата.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

IMOL_VALUE_TYPE = 'IMOL'


def _uf_messenger_chat_number_field() -> str:
    return str(getattr(settings, 'BITRIX_UF_MESSENGER_CHAT_NUMBER', 'UF_CRM_1742560088') or 'UF_CRM_1742560088')


def _uf_company_messenger_chat_number_field() -> str:
    return str(
        getattr(settings, 'BITRIX_UF_COMPANY_MESSENGER_CHAT_NUMBER', 'UF_CRM_1779436763') or 'UF_CRM_1779436763'
    )


def _uf_max_chat_id_field() -> str:
    return str(getattr(settings, 'BITRIX_UF_MAX_CHAT_ID', 'UF_CRM_1774872874') or 'UF_CRM_1774872874')


def _uf_company_max_chat_id_field() -> str:
    return str(getattr(settings, 'BITRIX_UF_COMPANY_MAX_CHAT_ID', 'UF_CRM_1779436775') or 'UF_CRM_1779436775')


def normalize_messenger_chat_number(value: Any) -> str:
    """Нормализация «1111» / «1111.0» из Bitrix и заголовка группы."""
    text = str(value or '').strip()
    if not text:
        return ''
    normalized = text.replace(',', '.').strip()
    try:
        number = float(normalized)
        if number.is_integer():
            return str(int(number))
    except (TypeError, ValueError):
        pass
    match = re.match(r'^(\d{1,6})', normalized)
    if match:
        return match.group(1)
    return normalized


def extract_group_number_from_title(title: str) -> str:
    """
    Из «1111 / LACONISTIQ / DECOPLUME ...» берёт префикс до первого «/».
    """
    text = str(title or '').strip()
    if not text:
        return ''
    head = text.split('/', 1)[0].strip()
    return normalize_messenger_chat_number(head)


def build_max_group_imol_value(*, chat_id: str) -> str:
    """IMOL для группы: только id чата, без user_id того, кто добавил бота."""
    normalized_chat_id = str(chat_id or '').strip()
    if not normalized_chat_id:
        return ''
    return f'max:g:{normalized_chat_id}'


def _im_entries(contact: dict) -> list[dict]:
    raw = contact.get('IM') or []
    if not isinstance(raw, list):
        return []
    result: list[dict] = []
    for item in raw:
        if isinstance(item, dict) and str(item.get('VALUE') or '').strip():
            entry = {
                'VALUE': str(item.get('VALUE') or '').strip(),
                'VALUE_TYPE': str(item.get('VALUE_TYPE') or 'OTHER').strip() or 'OTHER',
            }
            raw_id = item.get('ID') or item.get('id')
            if raw_id not in (None, ''):
                entry['ID'] = str(raw_id)
            result.append(entry)
    return result


def _im_already_has_chat(im_entries: list[dict], *, chat_id: str) -> bool:
    needle = str(chat_id or '').strip()
    if not needle:
        return False
    for item in im_entries:
        value = str(item.get('VALUE') or '')
        if needle in value and str(item.get('VALUE_TYPE') or '').upper() == IMOL_VALUE_TYPE:
            return True
    return False


def merge_imol_entry(existing_im: list[dict], imol_value: str) -> tuple[list[dict], bool]:
    """
    Добавляет IMOL-значение к существующим IM. Возвращает (новый список, already_present).
    """
    normalized_value = str(imol_value or '').strip()
    if not normalized_value:
        return existing_im, False
    merged = list(existing_im)
    for item in merged:
        if (
            str(item.get('VALUE') or '').strip() == normalized_value
            and str(item.get('VALUE_TYPE') or '').upper() == IMOL_VALUE_TYPE
        ):
            return merged, True
    merged.append({'VALUE': normalized_value, 'VALUE_TYPE': IMOL_VALUE_TYPE})
    return merged, False


def _append_entity_match(
    found: list[dict],
    seen: set[int],
    *,
    entity_id: int,
    name: str,
    source: str,
) -> None:
    if entity_id in seen:
        return
    seen.add(entity_id)
    found.append({'id': entity_id, 'name': name, 'source': source})


def find_contacts_by_messenger_chat_number(group_number: str) -> list[dict]:
    """
    Ищет контакты по полю «Номер чата в мессенджере» (снапшот + Bitrix API).
    Возвращает список dict: {id, name, source}.
    """
    normalized = normalize_messenger_chat_number(group_number)
    if not normalized:
        return []

    found: list[dict] = []
    seen: set[int] = set()

    try:
        from mailings.models import BitrixContactSnapshot
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group sync: snapshot import failed: %s', exc)
        BitrixContactSnapshot = None  # type: ignore[misc, assignment]

    if BitrixContactSnapshot is not None:
        variants = {normalized}
        if normalized.isdigit():
            variants.add(f'{normalized}.0')
        for row in BitrixContactSnapshot.objects.filter(messenger_chat_number__in=list(variants)).only(
            'bitrix_contact_id',
            'full_name',
            'messenger_chat_number',
        ):
            if normalize_messenger_chat_number(row.messenger_chat_number) != normalized:
                continue
            _append_entity_match(
                found,
                seen,
                entity_id=int(row.bitrix_contact_id),
                name=row.full_name or f'Contact #{row.bitrix_contact_id}',
                source='snapshot',
            )

    uf_field = _uf_messenger_chat_number_field()
    try:
        from mailings.services.bitrix import call_bitrix
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group sync: bitrix client import failed: %s', exc)
        return found

    try:
        response = call_bitrix(
            'crm.contact.list',
            {
                'filter': {uf_field: normalized},
                'select': ['ID', 'NAME', 'LAST_NAME', uf_field],
            },
        )
        items = response.get('result') or []
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group sync: crm.contact.list failed: %s', exc)
        return found

    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get('ID') or item.get('id')
        try:
            cid = int(raw_id)
        except (TypeError, ValueError):
            continue
        name = ' '.join(
            part for part in [item.get('LAST_NAME'), item.get('NAME')] if isinstance(part, str) and part.strip()
        ).strip()
        _append_entity_match(
            found,
            seen,
            entity_id=cid,
            name=name or f'Contact #{cid}',
            source='bitrix',
        )

    return found


def find_companies_by_messenger_chat_number(group_number: str) -> list[dict]:
    """
    Ищет компании по UF «Номер чата в мессенджере» (снапшот + Bitrix API).
    Возвращает список dict: {id, name, source}.
    """
    normalized = normalize_messenger_chat_number(group_number)
    if not normalized:
        return []

    found: list[dict] = []
    seen: set[int] = set()

    try:
        from mailings.models import BitrixCompanySnapshot
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group sync: company snapshot import failed: %s', exc)
        BitrixCompanySnapshot = None  # type: ignore[misc, assignment]

    if BitrixCompanySnapshot is not None:
        variants = {normalized}
        if normalized.isdigit():
            variants.add(f'{normalized}.0')
        for row in BitrixCompanySnapshot.objects.filter(messenger_chat_number__in=list(variants)).only(
            'bitrix_company_id',
            'title',
            'messenger_chat_number',
        ):
            if normalize_messenger_chat_number(row.messenger_chat_number) != normalized:
                continue
            _append_entity_match(
                found,
                seen,
                entity_id=int(row.bitrix_company_id),
                name=row.title or f'Company #{row.bitrix_company_id}',
                source='snapshot',
            )

    uf_field = _uf_company_messenger_chat_number_field()
    try:
        from mailings.services.bitrix import call_bitrix
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group sync: bitrix client import failed: %s', exc)
        return found

    try:
        response = call_bitrix(
            'crm.company.list',
            {
                'filter': {uf_field: normalized},
                'select': ['ID', 'TITLE', uf_field],
            },
        )
        items = response.get('result') or []
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group sync: crm.company.list failed: %s', exc)
        return found

    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get('ID') or item.get('id')
        try:
            company_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        title = str(item.get('TITLE') or '').strip()
        _append_entity_match(
            found,
            seen,
            entity_id=company_id,
            name=title or f'Company #{company_id}',
            source='bitrix',
        )

    return found


def _sync_max_group_to_single_contact(
    *,
    contact_id: int,
    normalized_chat_id: str,
    group_number: str,
    update_type: str,
) -> dict:
    imol_value = build_max_group_imol_value(chat_id=normalized_chat_id)
    partner_chat_id = f'max:{normalized_chat_id}'

    try:
        from mailings.services.bitrix import get_contact, update_contact
        from mailings.services.bitrix_sync import sync_contact_by_id
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group sync: bitrix import failed: %s', exc)
        return {'ok': False, 'error': f'import failed: {exc}'}

    contact = get_contact(contact_id) or {}
    if not contact:
        return {
            'ok': False,
            'error': 'crm.contact.get returned empty',
            'contact_id': contact_id,
            'group_number': group_number,
        }

    existing_im = _im_entries(contact)
    if _im_already_has_chat(existing_im, chat_id=normalized_chat_id):
        logger.info(
            'MAX group sync skipped: contact=%s already has IMOL for chat_id=%s',
            contact_id,
            normalized_chat_id,
        )
        sync_contact_by_id(str(contact_id))
        return {
            'ok': True,
            'skipped': True,
            'reason': 'imol_already_present',
            'contact_id': contact_id,
            'chat_id': normalized_chat_id,
            'group_number': group_number,
            'max_chat_id': partner_chat_id,
        }

    merged_im, already_exact = merge_imol_entry(existing_im, imol_value)
    fields: dict[str, Any] = {
        'IM': merged_im,
        _uf_max_chat_id_field(): partner_chat_id,
    }

    updated, update_error = update_contact(contact_id, fields)
    if not updated:
        return {
            'ok': False,
            'error': 'crm.contact.update failed',
            'bitrix_error': update_error,
            'contact_id': contact_id,
            'imol_value': imol_value,
            _uf_max_chat_id_field(): partner_chat_id,
        }

    try:
        sync_contact_by_id(str(contact_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            'MAX group sync: snapshot refresh failed contact_id=%s: %s',
            contact_id,
            exc,
        )

    try:
        from mailings.models import BitrixContactSnapshot

        BitrixContactSnapshot.objects.filter(bitrix_contact_id=contact_id).update(max_chat_id=partner_chat_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug('MAX group sync: local contact snapshot max_chat_id update failed: %s', exc)

    logger.info(
        'MAX group sync OK (contact): contact_id=%s group_number=%s chat_id=%s imol=%s update_type=%s',
        contact_id,
        group_number,
        normalized_chat_id,
        imol_value,
        update_type,
    )
    return {
        'ok': True,
        'contact_id': contact_id,
        'group_number': group_number,
        'chat_id': normalized_chat_id,
        'imol_value': imol_value,
        'imol_added': not already_exact,
        'max_chat_id': partner_chat_id,
        'update_type': update_type,
    }


def _sync_max_group_to_single_company(
    *,
    company_id: int,
    normalized_chat_id: str,
    group_number: str,
    update_type: str,
) -> dict:
    partner_chat_id = f'max:{normalized_chat_id}'
    uf_max_field = _uf_company_max_chat_id_field()

    try:
        from mailings.services.bitrix import call_bitrix, update_company
        from mailings.services.bitrix_sync import sync_company_by_id
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group sync: bitrix import failed: %s', exc)
        return {'ok': False, 'error': f'import failed: {exc}'}

    try:
        response = call_bitrix('crm.company.get', {'id': str(company_id)})
        company = response.get('result') or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group sync: crm.company.get failed company=%s: %s', company_id, exc)
        company = {}

    if not company:
        return {
            'ok': False,
            'error': 'crm.company.get returned empty',
            'company_id': company_id,
            'group_number': group_number,
        }

    current_value = str(company.get(uf_max_field) or '').strip()
    if current_value == partner_chat_id:
        logger.info(
            'MAX group sync skipped: company=%s already has %s=%s',
            company_id,
            uf_max_field,
            partner_chat_id,
        )
        sync_company_by_id(str(company_id))
        return {
            'ok': True,
            'skipped': True,
            'reason': 'max_chat_id_already_present',
            'company_id': company_id,
            'chat_id': normalized_chat_id,
            'group_number': group_number,
            'max_chat_id': partner_chat_id,
        }

    updated, update_error = update_company(company_id, {uf_max_field: partner_chat_id})
    if not updated:
        return {
            'ok': False,
            'error': 'crm.company.update failed',
            'bitrix_error': update_error,
            'company_id': company_id,
            uf_max_field: partner_chat_id,
        }

    try:
        sync_company_by_id(str(company_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            'MAX group sync: snapshot refresh failed company_id=%s: %s',
            company_id,
            exc,
        )

    try:
        from mailings.models import BitrixCompanySnapshot

        BitrixCompanySnapshot.objects.filter(bitrix_company_id=company_id).update(max_chat_id=partner_chat_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug('MAX group sync: local company snapshot max_chat_id update failed: %s', exc)

    logger.info(
        'MAX group sync OK (company): company_id=%s group_number=%s chat_id=%s %s=%s update_type=%s',
        company_id,
        group_number,
        normalized_chat_id,
        uf_max_field,
        partner_chat_id,
        update_type,
    )
    return {
        'ok': True,
        'company_id': company_id,
        'group_number': group_number,
        'chat_id': normalized_chat_id,
        'max_chat_id': partner_chat_id,
        'update_type': update_type,
    }


def sync_max_group_to_bitrix_contact(
    *,
    chat_id: str,
    title: str = '',
    update_type: str = '',
) -> dict:
    """
    Находит контакт и/или компанию по номеру из title.
    Контакт: IMOL + UF max_chat_id. Компания: UF max_chat_id (UF_CRM_1779436775).
    """
    normalized_chat_id = str(chat_id or '').strip()
    if not normalized_chat_id:
        return {'ok': False, 'error': 'chat_id is empty'}

    group_number = extract_group_number_from_title(title)
    if not group_number:
        return {
            'ok': False,
            'error': 'group_number not found in title',
            'chat_id': normalized_chat_id,
            'title': title,
            'update_type': update_type,
        }

    contact_matches = find_contacts_by_messenger_chat_number(group_number)
    company_matches = find_companies_by_messenger_chat_number(group_number)

    if not contact_matches and not company_matches:
        return {
            'ok': False,
            'error': 'contact/company not found by messenger_chat_number',
            'chat_id': normalized_chat_id,
            'group_number': group_number,
            'title': title,
        }

    if len(contact_matches) > 1:
        return {
            'ok': False,
            'error': 'multiple contacts with same messenger_chat_number',
            'chat_id': normalized_chat_id,
            'group_number': group_number,
            'matches': contact_matches,
        }

    if len(company_matches) > 1:
        return {
            'ok': False,
            'error': 'multiple companies with same messenger_chat_number',
            'chat_id': normalized_chat_id,
            'group_number': group_number,
            'matches': company_matches,
        }

    contact_result: dict | None = None
    company_result: dict | None = None

    if contact_matches:
        contact_result = _sync_max_group_to_single_contact(
            contact_id=int(contact_matches[0]['id']),
            normalized_chat_id=normalized_chat_id,
            group_number=group_number,
            update_type=update_type,
        )
        if not contact_result.get('ok'):
            return {
                **contact_result,
                'chat_id': normalized_chat_id,
                'group_number': group_number,
                'entity': 'contact',
            }

    if company_matches:
        company_result = _sync_max_group_to_single_company(
            company_id=int(company_matches[0]['id']),
            normalized_chat_id=normalized_chat_id,
            group_number=group_number,
            update_type=update_type,
        )
        if not company_result.get('ok'):
            return {
                **company_result,
                'chat_id': normalized_chat_id,
                'group_number': group_number,
                'entity': 'company',
            }

    overall_ok = True
    if contact_result is not None and not contact_result.get('ok'):
        overall_ok = False
    if company_result is not None and not company_result.get('ok'):
        overall_ok = False

    if not overall_ok:
        return {
            'ok': False,
            'error': 'partial sync failure',
            'chat_id': normalized_chat_id,
            'group_number': group_number,
            'contact': contact_result,
            'company': company_result,
        }

    result: dict[str, Any] = {
        'ok': True,
        'group_number': group_number,
        'chat_id': normalized_chat_id,
        'max_chat_id': f'max:{normalized_chat_id}',
        'update_type': update_type,
    }
    if contact_matches:
        result['contact_id'] = int(contact_matches[0]['id'])
        result['contact_name'] = contact_matches[0].get('name', '')
        if contact_result:
            result['contact'] = contact_result
            result['imol_value'] = contact_result.get('imol_value')
            result['imol_added'] = contact_result.get('imol_added')
    if company_matches:
        result['company_id'] = int(company_matches[0]['id'])
        result['company_name'] = company_matches[0].get('name', '')
        if company_result:
            result['company'] = company_result

    return result
