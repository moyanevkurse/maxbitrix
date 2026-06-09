"""
Извлечение телефонов из карточек Bitrix для резолва MAX user_id.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def normalize_phone(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"[^\d+]", "", raw)
    if cleaned.startswith("8") and len(cleaned) == 11:
        cleaned = f"+7{cleaned[1:]}"
    elif cleaned.startswith("7") and len(cleaned) == 11:
        cleaned = f"+{cleaned}"
    elif cleaned.startswith("9") and len(cleaned) == 10:
        cleaned = f"+7{cleaned}"
    if cleaned and not cleaned.startswith("+") and cleaned.isdigit():
        cleaned = f"+{cleaned}"
    return cleaned


def extract_mailing_phones(record: dict) -> list[str]:
    """Телефоны с VALUE_TYPE=MAILING (для рассылок) из поля PHONE сущности CRM."""
    result: list[str] = []
    seen: set[str] = set()
    phones = record.get("PHONE") or []
    if not isinstance(phones, list):
        phones = [phones] if phones else []
    for item in phones:
        if not isinstance(item, dict):
            continue
        value_type = str(item.get("VALUE_TYPE") or "").strip().upper()
        if value_type != "MAILING":
            continue
        normalized = normalize_phone(str(item.get("VALUE") or "").strip())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def extract_phones_by_value_type(record: dict, value_type_name: str) -> list[str]:
    """Телефоны из поля PHONE по конкретному VALUE_TYPE."""
    result: list[str] = []
    seen: set[str] = set()
    phones = record.get("PHONE") or []
    if not isinstance(phones, list):
        phones = [phones] if phones else []
    expected = str(value_type_name or "").strip().upper()
    if not expected:
        return []
    for item in phones:
        if not isinstance(item, dict):
            continue
        value_type = str(item.get("VALUE_TYPE") or "").strip().upper()
        if value_type != expected:
            continue
        normalized = normalize_phone(str(item.get("VALUE") or "").strip())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def extract_all_phones(record: dict) -> list[str]:
    """Все телефоны из поля PHONE (любой VALUE_TYPE)."""
    result: list[str] = []
    seen: set[str] = set()
    phones = record.get("PHONE") or []
    if not isinstance(phones, list):
        phones = [phones] if phones else []
    for item in phones:
        value = ""
        if isinstance(item, dict):
            value = str(item.get("VALUE") or "").strip()
        elif isinstance(item, str):
            value = item.strip()
        normalized = normalize_phone(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _get_company(company_id: str) -> dict:
    from mailings.services.bitrix import call_bitrix

    normalized_id = str(company_id or "").strip()
    if not normalized_id.isdigit():
        return {}
    try:
        response = call_bitrix("crm.company.get", {"id": normalized_id})
        return response.get("result") or {}
    except Exception:
        try:
            response = call_bitrix("crm.company.get", {"ID": normalized_id})
            return response.get("result") or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("crm.company.get failed for company=%s: %s", normalized_id, exc)
            return {}


def resolve_company_mailing_phones(company_id: str) -> list[str]:
    company = _get_company(company_id)
    if not company:
        return []
    return extract_mailing_phones(company)


def resolve_company_group_phones(company_id: str) -> list[str]:
    """
    Телефоны компании для создания MAX-группы:
    только PHONE с VALUE_TYPE=MAILING.
    """
    company = _get_company(company_id)
    if not company:
        return []
    return extract_mailing_phones(company)


def resolve_entity_phones(entity_type: str, entity_id: str) -> list[str]:
    """
    Список телефонов для phone->MAX user_id резолва.
    - contact: все PHONE контакта
    - company: только PHONE с VALUE_TYPE=MAILING на карточке компании
    """
    normalized_type = str(entity_type or "").strip().lower()
    normalized_id = str(entity_id or "").strip()
    if normalized_type not in {"contact", "company"} or not normalized_id.isdigit():
        return []

    try:
        from mailings.services.bitrix import get_contact
    except Exception as exc:  # noqa: BLE001
        logger.warning("MAX phone resolve failed: bitrix client import error: %s", exc)
        return []

    if normalized_type == "contact":
        contact = get_contact(normalized_id) or {}
        return extract_all_phones(contact)

    return resolve_company_group_phones(normalized_id)
