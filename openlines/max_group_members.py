"""
Участники MAX-группы при создании из контакта или компании:
контакт — клиент (IMOL) + ответственный + сотрудники из админ-справочника;
компания — ответственный + сотрудники из справочника (+ MAILING-телефоны в Celery).
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from django.conf import settings

RosterKind = Literal['contact', 'company']

logger = logging.getLogger(__name__)


def _value_to_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value).strip()
    if isinstance(value, dict):
        for key in ('VALUE', 'value', 'TITLE', 'title', 'NAME', 'name'):
            text = str(value.get(key) or '').strip()
            if text:
                return text
    if isinstance(value, list):
        for item in value:
            text = _value_to_text(item)
            if text:
                return text
    return ''


def parse_max_user_id_from_value(value) -> int | None:
    """Из UF/строки: 12345, max:12345, max:dm:12345."""
    text = _value_to_text(value)
    if not text:
        return None
    match = re.search(r'max:dm:(\d+)', text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r'max:(\d+)\s*$', text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    if text.isdigit():
        parsed = int(text)
        return parsed if parsed > 0 else None
    return None


def extract_max_user_ids_from_contact(contact: dict) -> list[int]:
    """
    MAX user_id из IM (IMOL) контакта:
    max:g:...:u:<id>, max:dm:<id>, max:<id>.
    """
    im_values = contact.get('IM') or []
    if not isinstance(im_values, list):
        return []

    found: list[int] = []
    seen: set[int] = set()
    for item in im_values:
        if not isinstance(item, dict):
            continue
        value_type = str(item.get('VALUE_TYPE') or '').upper()
        value = str(item.get('VALUE') or '').strip()
        if value_type != 'IMOL' or not value:
            continue

        match_user = re.search(r'max:g:[^:]+:u:(\d+)', value)
        if match_user:
            user_id = int(match_user.group(1))
            if user_id not in seen:
                seen.add(user_id)
                found.append(user_id)
            continue

        match_dm = re.search(r'max:dm:(\d+)', value)
        if match_dm:
            user_id = int(match_dm.group(1))
            if user_id not in seen:
                seen.add(user_id)
                found.append(user_id)
            continue

        match_legacy = re.search(r'max:(\d+)$', value)
        if match_legacy:
            user_id = int(match_legacy.group(1))
            if user_id not in seen:
                seen.add(user_id)
                found.append(user_id)

    return found


def _append_user_id(result: list[int], seen: set[int], user_id: int | None) -> None:
    if not user_id or user_id <= 0 or user_id in seen:
        return
    seen.add(user_id)
    result.append(user_id)


def _employee_max_user_id_from_contact(contact: dict) -> int | None:
    uf_key = str(getattr(settings, 'BITRIX_UF_EMPLOYEE_MAX_USER_ID', '') or '').strip()
    if uf_key:
        parsed = parse_max_user_id_from_value(contact.get(uf_key))
        if parsed:
            return parsed
    for user_id in extract_max_user_ids_from_contact(contact):
        return user_id
    return None


def resolve_assigned_manager_max_user_id(assigned_by_id: str) -> int | None:
    """
    Ответственный по контакту (ASSIGNED_BY_ID) → MAX user_id.
    1) запись в справочнике сотрудников по bitrix_user_id
    2) UF на карточке сотрудника-контакта в Bitrix
    3) UF на user.get (если задан BITRIX_UF_USER_MAX_USER_ID)
    """
    user_id_str = str(assigned_by_id or '').strip()
    if not user_id_str.isdigit():
        return None

    try:
        from .models import MaxGroupStaffMember

        staff = (
            MaxGroupStaffMember.objects.filter(is_active=True, bitrix_user_id=user_id_str)
            .order_by('sort_order', 'id')
            .first()
        )
        if staff and staff.max_user_id:
            return int(staff.max_user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group manager: staff lookup failed: %s', exc)

    try:
        from mailings.services.bitrix import find_employee_contact_by_bitrix_user_id, get_user

        employee_contact = find_employee_contact_by_bitrix_user_id(user_id_str) or {}
        if employee_contact:
            parsed = _employee_max_user_id_from_contact(employee_contact)
            if parsed:
                return parsed

        user = get_user(user_id_str) or {}
        configured_uf = str(getattr(settings, 'BITRIX_UF_USER_MAX_USER_ID', '') or '').strip()
        uf_candidates = [field for field in [configured_uf, 'UF_USR_1779878090247'] if field]
        for uf_user_max in uf_candidates:
            parsed = parse_max_user_id_from_value(user.get(uf_user_max))
            if parsed:
                return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            'MAX group manager resolve failed: assigned_by=%s error=%s',
            user_id_str,
            exc,
        )

    return None


def _staff_roster_limit(roster: RosterKind) -> int:
    if roster == 'company':
        company_limit = int(getattr(settings, 'MAX_GROUP_COMPANY_STAFF_ROSTER_LIMIT', 0) or 0)
        if company_limit > 0:
            return company_limit
    explicit = int(getattr(settings, 'MAX_GROUP_STAFF_ROSTER_LIMIT', 0) or 0)
    if explicit > 0:
        return explicit
    return max(int(getattr(settings, 'MAX_GROUP_CONTACT_STAFF_ROSTER_LIMIT', 30) or 0), 0)


def _roster_scope_filter(roster: RosterKind) -> dict[str, bool]:
    if roster == 'contact':
        return {'add_for_contact': True}
    return {'add_for_company': True}


def _company_add_staff_roster() -> bool:
    return bool(
        getattr(
            settings,
            'MAX_GROUP_COMPANY_ADD_STAFF_ROSTER',
            getattr(settings, 'MAX_GROUP_CONTACT_ADD_COMPANY_MEMBERS', True),
        )
    )


def resolve_roster_staff_user_ids(roster: RosterKind) -> list[int]:
    """Сотрудники из админ-справочника MaxGroupStaffMember для контакта или компании."""
    limit = _staff_roster_limit(roster)
    if limit <= 0:
        return []

    try:
        from .models import MaxGroupStaffMember

        rows = (
            MaxGroupStaffMember.objects.filter(is_active=True, **_roster_scope_filter(roster))
            .exclude(max_user_id__isnull=True)
            .exclude(max_user_id=0)
            .order_by('sort_order', 'id')[:limit]
        )
        result: list[int] = []
        seen: set[int] = set()
        for row in rows:
            _append_user_id(result, seen, int(row.max_user_id))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group roster resolve failed: %s', exc)
        return []


def resolve_roster_staff_phones(roster: RosterKind) -> list[str]:
    """Телефоны сотрудников без max_user_id — для phone→user_id в Celery."""
    limit = _staff_roster_limit(roster)
    if limit <= 0:
        return []

    from .entity_phones import normalize_phone

    try:
        from .models import MaxGroupStaffMember

        from django.db.models import Q

        rows = (
            MaxGroupStaffMember.objects.filter(is_active=True, **_roster_scope_filter(roster))
            .filter(Q(max_user_id__isnull=True) | Q(max_user_id=0))
            .order_by('sort_order', 'id')[:limit]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group roster phones failed: %s', exc)
        return []

    phones: list[str] = []
    seen: set[str] = set()
    for row in rows[:limit]:
        phone = normalize_phone(row.phone)
        if phone and phone not in seen:
            seen.add(phone)
            phones.append(phone)
    return phones


def resolve_manager_phone_for_assigned(assigned_by_id: str) -> str:
    """Телефон ответственного, если MAX user_id не найден в Bitrix/справочнике."""
    user_id_str = str(assigned_by_id or '').strip()
    if not user_id_str.isdigit():
        return ''

    from .entity_phones import normalize_phone

    try:
        from .models import MaxGroupStaffMember

        staff = (
            MaxGroupStaffMember.objects.filter(is_active=True, bitrix_user_id=user_id_str)
            .order_by('sort_order', 'id')
            .first()
        )
        if staff and not staff.max_user_id:
            return normalize_phone(staff.phone)
    except Exception:  # noqa: BLE001
        pass

    try:
        from mailings.services.bitrix import find_employee_contact_by_bitrix_user_id, get_user
        from .entity_phones import extract_all_phones

        employee = find_employee_contact_by_bitrix_user_id(user_id_str) or {}
        phones = extract_all_phones(employee)
        if phones:
            return phones[0]

        user = get_user(user_id_str) or {}
        for key in ('PERSONAL_MOBILE', 'WORK_PHONE', 'PERSONAL_PHONE'):
            phone = normalize_phone(str(user.get(key) or ''))
            if phone:
                return phone
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group manager phone resolve failed: assigned_by=%s: %s', user_id_str, exc)

    return ''


def resolve_contact_group_user_ids(contact_id: str) -> list[int]:
    """
    Участники для группы из контакта:
    - клиент (IMOL контакта)
    - ответственный менеджер
    - сотрудники из справочника MaxGroupStaffMember
    """
    normalized_id = str(contact_id or '').strip()
    if not normalized_id.isdigit():
        return []

    if not bool(getattr(settings, 'MAX_GROUP_CONTACT_ADD_COMPANY_MEMBERS', True)):
        try:
            from mailings.services.bitrix import get_contact

            contact = get_contact(normalized_id) or {}
            return extract_max_user_ids_from_contact(contact)
        except Exception:  # noqa: BLE001
            return []

    try:
        from mailings.services.bitrix import get_contact
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group contact members: bitrix import error: %s', exc)
        return []

    contact = get_contact(normalized_id) or {}
    result: list[int] = []
    seen: set[int] = set()

    contact_imol_ids = extract_max_user_ids_from_contact(contact)
    for user_id in contact_imol_ids:
        _append_user_id(result, seen, user_id)

    assigned_by = str(contact.get('ASSIGNED_BY_ID') or '').strip()
    manager_id = resolve_assigned_manager_max_user_id(assigned_by)
    _append_user_id(result, seen, manager_id)

    roster_ids = resolve_roster_staff_user_ids('contact')
    for user_id in roster_ids:
        _append_user_id(result, seen, user_id)

    logger.info(
        'MAX group contact members: contact=%s imol_ids=%s assigned_by=%s manager_id=%s roster_ids=%s final_ids=%s',
        normalized_id,
        contact_imol_ids,
        assigned_by,
        manager_id,
        roster_ids,
        result,
    )
    return result


def resolve_contact_group_extra_phones(contact_id: str) -> list[str]:
    """
    Телефоны для fallback phone→MAX, если user_id не задан:
    ответственный + сотрудники справочника без max_user_id.
    """
    if not bool(getattr(settings, 'MAX_GROUP_CONTACT_ADD_COMPANY_MEMBERS', True)):
        return []

    normalized_id = str(contact_id or '').strip()
    if not normalized_id.isdigit():
        return []

    try:
        from mailings.services.bitrix import get_contact
    except Exception:  # noqa: BLE001
        return []

    contact = get_contact(normalized_id) or {}
    assigned_by = str(contact.get('ASSIGNED_BY_ID') or '').strip()

    phones: list[str] = []
    seen: set[str] = set()

    manager_phone = resolve_manager_phone_for_assigned(assigned_by)
    if manager_phone and manager_phone not in seen:
        seen.add(manager_phone)
        phones.append(manager_phone)

    for phone in resolve_roster_staff_phones('contact'):
        if phone not in seen:
            seen.add(phone)
            phones.append(phone)

    return phones


def _get_company(company_id: str) -> dict:
    try:
        from mailings.services.bitrix import call_bitrix
    except Exception as exc:  # noqa: BLE001
        logger.warning('MAX group company members: bitrix import error: %s', exc)
        return {}

    normalized_id = str(company_id or '').strip()
    if not normalized_id.isdigit():
        return {}
    try:
        response = call_bitrix('crm.company.get', {'id': normalized_id})
        return response.get('result') or {}
    except Exception:
        try:
            response = call_bitrix('crm.company.get', {'ID': normalized_id})
            return response.get('result') or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning('MAX group company get failed: company=%s error=%s', normalized_id, exc)
            return {}


def resolve_company_group_user_ids(company_id: str) -> list[int]:
    """
    Участники для группы из компании:
    - ответственный (ASSIGNED_BY_ID);
    - сотрудники из справочника MaxGroupStaffMember (как при создании из контакта).
    """
    normalized_id = str(company_id or '').strip()
    if not normalized_id.isdigit():
        return []

    company = _get_company(normalized_id)
    assigned_by = str(company.get('ASSIGNED_BY_ID') or '').strip()
    result: list[int] = []
    seen: set[int] = set()

    manager_id = resolve_assigned_manager_max_user_id(assigned_by)
    _append_user_id(result, seen, manager_id)

    roster_ids: list[int] = []
    if _company_add_staff_roster():
        roster_ids = resolve_roster_staff_user_ids('company')
        for user_id in roster_ids:
            _append_user_id(result, seen, user_id)

    logger.info(
        'MAX group company members: company=%s assigned_by=%s manager_id=%s roster_ids=%s final_ids=%s',
        normalized_id,
        assigned_by,
        manager_id,
        roster_ids,
        result,
    )
    return result


def resolve_company_group_extra_phones(company_id: str) -> list[str]:
    """
    Телефоны для phone->MAX fallback при создании из компании:
    ответственный + сотрудники справочника без max_user_id.
    """
    normalized_id = str(company_id or '').strip()
    if not normalized_id.isdigit():
        return []

    company = _get_company(normalized_id)
    assigned_by = str(company.get('ASSIGNED_BY_ID') or '').strip()

    phones: list[str] = []
    seen: set[str] = set()

    manager_phone = resolve_manager_phone_for_assigned(assigned_by)
    if manager_phone and manager_phone not in seen:
        seen.add(manager_phone)
        phones.append(manager_phone)

    if _company_add_staff_roster():
        for phone in resolve_roster_staff_phones('company'):
            if phone not in seen:
                seen.add(phone)
                phones.append(phone)

    return phones
