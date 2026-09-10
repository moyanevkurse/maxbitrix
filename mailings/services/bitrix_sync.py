from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.utils.dateparse import parse_datetime

from mailings.models import BitrixCompanySnapshot, BitrixContactSnapshot
from django.conf import settings

from mailings.services.bitrix import call_bitrix
from mailings.services.bitrix_address import pick_company_address, pick_contact_address

logger = logging.getLogger(__name__)
BITRIX_BATCH_SIZE = 50


def _contact_messenger_chat_uf() -> str:
    return str(getattr(settings, 'BITRIX_UF_MESSENGER_CHAT_NUMBER', 'UF_CRM_1742560088') or 'UF_CRM_1742560088')


def _contact_max_chat_uf() -> str:
    return str(getattr(settings, 'BITRIX_UF_MAX_CHAT_ID', 'UF_CRM_1774872874') or 'UF_CRM_1774872874')


def _company_messenger_chat_uf() -> str:
    return str(
        getattr(settings, 'BITRIX_UF_COMPANY_MESSENGER_CHAT_NUMBER', 'UF_CRM_1779436763') or 'UF_CRM_1779436763'
    )


def _company_max_chat_uf() -> str:
    return str(getattr(settings, 'BITRIX_UF_COMPANY_MAX_CHAT_ID', 'UF_CRM_1779436775') or 'UF_CRM_1779436775')

# Локальные маппинги (ID -> label) для iblock_element полей,
# так как lists.element.get недоступен на текущем тарифе Bitrix.
POTENTIAL_MOYSKLAD_LABELS = {
    "": "",
    "579": "А",
    "581": "B",
    "583": "C",
    "585": "Не присвоен",
}

COMPANY_SEGMENT_LABELS = {
    "": "",
    "1163": "Дистрибьютер",
    "1165": "Дилер",
    "1167": "Дизайнер",
    "1169": "Розничный",
    "1171": "Поставщик",
}

# Тип контакта (UF_CRM_1741120826) → пишем в job_title.
CONTACT_JOB_TITLE_UF = "UF_CRM_1741120826"
CONTACT_JOB_TITLE_LABELS = {
    "": "",
    "45": "Розничный клиент",
    "47": "Дизайнер",
    "49": "Партнер",
}


@dataclass
class SyncStats:
    contacts_total: int = 0
    contacts_saved: int = 0
    companies_total: int = 0
    companies_saved: int = 0


def _normalize_phone(value: Any) -> str:
    if isinstance(value, list) and value:
        first = value[0] or {}
        if isinstance(first, dict):
            return str(first.get("VALUE") or "").strip()
        return str(first).strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def _normalize_email(value: Any) -> str:
    if isinstance(value, list) and value:
        first = value[0] or {}
        if isinstance(first, dict):
            return str(first.get("VALUE") or "").strip()
        return str(first).strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def _full_name(contact: dict[str, Any]) -> str:
    parts = [
        str(contact.get("LAST_NAME") or "").strip(),
        str(contact.get("NAME") or "").strip(),
        str(contact.get("SECOND_NAME") or "").strip(),
    ]
    return " ".join([p for p in parts if p]).strip()


def _use_requisite_addresses() -> bool:
    return bool(getattr(settings, 'BITRIX_ADDRESS_USE_REQUISITES', True))


def _pick_company_city(company: dict[str, Any]) -> str:
    candidates = [
        company.get("UF_CRM_1747678283"),
        company.get("UF_CRM_1742561008637"),
        company.get("UF_CRM_680A1B15C9661"),
        company.get("ADDRESS_CITY"),
        company.get("REG_ADDRESS_CITY"),
        company.get("ADDRESS"),
        company.get("REG_ADDRESS"),
        company.get("ADDRESS_LEGAL"),
    ]
    for item in candidates:
        text = str(item or "").strip()
        if text:
            return text
    return ""


def _parse_dt(value: Any):
    text = str(value or "").strip()
    if not text:
        return None
    return parse_datetime(text)


def resolve_potential_label(value_id: Any) -> str:
    return POTENTIAL_MOYSKLAD_LABELS.get(str(value_id or "").strip(), "")


def resolve_segment_label(value_id: Any) -> str:
    return COMPANY_SEGMENT_LABELS.get(str(value_id or "").strip(), "")


def resolve_contact_job_title_label(value_id: Any) -> str:
    return CONTACT_JOB_TITLE_LABELS.get(str(value_id or "").strip(), "")


def _first_enum_id(value: Any) -> str:
    """Первый ID из UF-списка/скаляра Bitrix (поле обычно одно значение)."""
    if isinstance(value, list):
        if not value:
            return ""
        first = value[0]
        if isinstance(first, dict):
            return str(first.get("VALUE") or first.get("ID") or "").strip()
        return str(first or "").strip()
    if isinstance(value, dict):
        return str(value.get("VALUE") or value.get("ID") or "").strip()
    return str(value or "").strip()


def resolve_contact_job_title(item: dict[str, Any]) -> str:
    value_id = _first_enum_id(item.get(CONTACT_JOB_TITLE_UF))
    return resolve_contact_job_title_label(value_id)


def _list_all(method: str, *, select: list[str] | None = None, filter_data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    start = 0
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        params: dict[str, Any] = {"start": start}
        if select:
            params["select"] = select
        if filter_data:
            params["filter"] = filter_data
        payload = call_bitrix(method, params)
        batch = payload.get("result") or []
        if not isinstance(batch, list):
            break
        items.extend(batch)
        logger.info(
            "Bitrix page loaded: method=%s page=%s start=%s batch=%s total_loaded=%s",
            method,
            page,
            start,
            len(batch),
            len(items),
        )
        next_start = payload.get("next")
        if next_start is None:
            break
        start = int(next_start)
        page += 1
    return items


def _batch_get_by_ids(get_method: str, ids: list[int]) -> list[dict[str, Any]]:
    """
    Получить детальные записи по ID через batch (до 50 команд за запрос).
    get_method: 'crm.contact.get' или 'crm.company.get'
    """
    detailed: list[dict[str, Any]] = []
    if not ids:
        return detailed

    for chunk_idx in range(0, len(ids), BITRIX_BATCH_SIZE):
        chunk = ids[chunk_idx:chunk_idx + BITRIX_BATCH_SIZE]
        cmd = {f"i{entity_id}": f"{get_method}?id={entity_id}" for entity_id in chunk}
        payload = call_bitrix("batch", {"cmd": cmd})
        result = (payload.get("result") or {}).get("result") or {}
        for entity_id in chunk:
            item = result.get(f"i{entity_id}")
            if isinstance(item, dict):
                detailed.append(item)
        logger.info(
            "Bitrix batch details loaded: method=%s chunk=%s size=%s loaded_total=%s",
            get_method,
            (chunk_idx // BITRIX_BATCH_SIZE) + 1,
            len(chunk),
            len(detailed),
        )
    return detailed


def sync_contacts(*, date_modify_from: str | None = None) -> tuple[int, int]:
    filter_data = {"!=ID": 0}
    if date_modify_from:
        filter_data[">=DATE_MODIFY"] = date_modify_from
    contact_rows = _list_all("crm.contact.list", select=["ID", "DATE_MODIFY"], filter_data=filter_data)
    contact_ids = [int(item.get("ID")) for item in contact_rows if item.get("ID")]
    contacts = _batch_get_by_ids("crm.contact.get", contact_ids)
    saved = 0
    for item in contacts:
        contact_id = int(item.get("ID"))
        obj, _ = BitrixContactSnapshot.objects.update_or_create(
            bitrix_contact_id=contact_id,
            defaults={
                "full_name": _full_name(item),
                "assigned_by_id": str(item.get("ASSIGNED_BY_ID") or ""),
                "phone": _normalize_phone(item.get("PHONE")),
                "email": _normalize_email(item.get("EMAIL")),
                "job_title": resolve_contact_job_title(item),
                "address": pick_contact_address(item, use_requisites=_use_requisite_addresses()),
                "messenger_chat_number": str(item.get(_contact_messenger_chat_uf()) or ""),
                "last_sale_at": _parse_dt(item.get("UF_CRM_CONTLASTSALE")),
                "telegram_id": str(item.get("UF_CRM_1770617684") or ""),
                "max_chat_id": str(item.get(_contact_max_chat_uf()) or ""),
                "bitrix_date_modified": _parse_dt(item.get("DATE_MODIFY")),
                "raw_data": item,
                "parsed_data": {
                    "name": str(item.get("NAME") or ""),
                    "last_name": str(item.get("LAST_NAME") or ""),
                    "second_name": str(item.get("SECOND_NAME") or ""),
                },
            },
        )
        saved += 1
        logger.debug("Bitrix contact synced: id=%s db_id=%s", contact_id, obj.pk)
        if saved % 100 == 0:
            logger.info("Bitrix contacts progress: saved=%s/%s", saved, len(contacts))
    return len(contacts), saved


def sync_companies(*, date_modify_from: str | None = None) -> tuple[int, int]:
    filter_data = {"!=ID": 0}
    if date_modify_from:
        filter_data[">=DATE_MODIFY"] = date_modify_from
    company_rows = _list_all("crm.company.list", select=["ID", "DATE_MODIFY"], filter_data=filter_data)
    company_ids = [int(item.get("ID")) for item in company_rows if item.get("ID")]
    companies = _batch_get_by_ids("crm.company.get", company_ids)
    saved = 0
    for item in companies:
        company_id = int(item.get("ID"))
        potential_id = str(item.get("UF_CRM_1742563768") or "")
        segment_id = str(item.get("UF_CRM_1747679145") or "")
        obj, _ = BitrixCompanySnapshot.objects.update_or_create(
            bitrix_company_id=company_id,
            defaults={
                "title": str(item.get("TITLE") or ""),
                "assigned_by_id": str(item.get("ASSIGNED_BY_ID") or ""),
                "address": pick_company_address(item, use_requisites=_use_requisite_addresses()),
                "potential_value_id": potential_id,
                "potential_value": resolve_potential_label(potential_id),
                "segment_value_id": segment_id,
                "segment_value": resolve_segment_label(segment_id),
                "last_sale_at": _parse_dt(item.get("UF_CRM_LASTSALE")),
                "messenger_chat_number": str(item.get(_company_messenger_chat_uf()) or ""),
                "max_chat_id": str(item.get(_company_max_chat_uf()) or ""),
                "bitrix_date_modified": _parse_dt(item.get("DATE_MODIFY")),
                "raw_data": item,
                "parsed_data": {
                    "groups_moysklad": str(item.get("UF_CRM_1747674019") or ""),
                    "city": _pick_company_city(item),
                    "sales_count": str(item.get("UF_CRM_SALES") or ""),
                    "sales_amount": str(item.get("UF_CRM_SALESAMOUNT") or ""),
                    "average_check": str(item.get("UF_CRM_AVERAGECHECK") or ""),
                    "balance": str(item.get("UF_CRM_BALANCE") or ""),
                },
            },
        )
        saved += 1
        logger.debug("Bitrix company synced: id=%s db_id=%s", company_id, obj.pk)
        if saved % 100 == 0:
            logger.info("Bitrix companies progress: saved=%s/%s", saved, len(companies))
    return len(companies), saved


def sync_bitrix_snapshots(*, date_modify_from: str | None = None) -> SyncStats:
    contacts_total, contacts_saved = sync_contacts(date_modify_from=date_modify_from)
    companies_total, companies_saved = sync_companies(date_modify_from=date_modify_from)
    stats = SyncStats(
        contacts_total=contacts_total,
        contacts_saved=contacts_saved,
        companies_total=companies_total,
        companies_saved=companies_saved,
    )
    logger.info(
        "Bitrix sync finished: contacts=%s/%s companies=%s/%s",
        stats.contacts_saved,
        stats.contacts_total,
        stats.companies_saved,
        stats.companies_total,
    )
    return stats


def sync_contact_by_id(contact_id: int | str) -> bool:
    """
    Точечный апдейт snapshot контакта по ID (для webhook ONCRMCONTACT*).
    Возвращает True, если контакт успешно сохранён.
    """
    contact_id_str = str(contact_id or "").strip()
    if not contact_id_str.isdigit():
        return False

    payload = call_bitrix("crm.contact.get", {"id": contact_id_str})
    item = payload.get("result") or {}
    if not isinstance(item, dict) or not item.get("ID"):
        return False

    bitrix_id = int(item.get("ID"))
    BitrixContactSnapshot.objects.update_or_create(
        bitrix_contact_id=bitrix_id,
        defaults={
            "full_name": _full_name(item),
            "assigned_by_id": str(item.get("ASSIGNED_BY_ID") or ""),
            "phone": _normalize_phone(item.get("PHONE")),
            "email": _normalize_email(item.get("EMAIL")),
            "job_title": resolve_contact_job_title(item),
            "address": pick_contact_address(item, use_requisites=_use_requisite_addresses()),
            "messenger_chat_number": str(item.get(_contact_messenger_chat_uf()) or ""),
            "last_sale_at": _parse_dt(item.get("UF_CRM_CONTLASTSALE")),
            "telegram_id": str(item.get("UF_CRM_1770617684") or ""),
            "max_chat_id": str(item.get(_contact_max_chat_uf()) or ""),
            "bitrix_date_modified": _parse_dt(item.get("DATE_MODIFY")),
            "raw_data": item,
            "parsed_data": {
                "name": str(item.get("NAME") or ""),
                "last_name": str(item.get("LAST_NAME") or ""),
                "second_name": str(item.get("SECOND_NAME") or ""),
            },
        },
    )
    return True


def sync_company_by_id(company_id: int | str) -> bool:
    """
    Точечный апдейт snapshot компании по ID (для webhook ONCRMCOMPANY*).
    Возвращает True, если компания успешно сохранена.
    """
    company_id_str = str(company_id or "").strip()
    if not company_id_str.isdigit():
        return False

    payload = call_bitrix("crm.company.get", {"id": company_id_str})
    item = payload.get("result") or {}
    if not isinstance(item, dict) or not item.get("ID"):
        return False

    bitrix_id = int(item.get("ID"))
    potential_id = str(item.get("UF_CRM_1742563768") or "")
    segment_id = str(item.get("UF_CRM_1747679145") or "")
    BitrixCompanySnapshot.objects.update_or_create(
        bitrix_company_id=bitrix_id,
        defaults={
            "title": str(item.get("TITLE") or ""),
            "assigned_by_id": str(item.get("ASSIGNED_BY_ID") or ""),
            "address": pick_company_address(item, use_requisites=_use_requisite_addresses()),
            "potential_value_id": potential_id,
            "potential_value": resolve_potential_label(potential_id),
            "segment_value_id": segment_id,
            "segment_value": resolve_segment_label(segment_id),
            "last_sale_at": _parse_dt(item.get("UF_CRM_LASTSALE")),
            "messenger_chat_number": str(item.get(_company_messenger_chat_uf()) or ""),
            "max_chat_id": str(item.get(_company_max_chat_uf()) or ""),
            "bitrix_date_modified": _parse_dt(item.get("DATE_MODIFY")),
            "raw_data": item,
            "parsed_data": {
                "groups_moysklad": str(item.get("UF_CRM_1747674019") or ""),
                "city": _pick_company_city(item),
                "sales_count": str(item.get("UF_CRM_SALES") or ""),
                "sales_amount": str(item.get("UF_CRM_SALESAMOUNT") or ""),
                "average_check": str(item.get("UF_CRM_AVERAGECHECK") or ""),
                "balance": str(item.get("UF_CRM_BALANCE") or ""),
            },
        },
    )
    return True


def delete_contact_snapshot(contact_id: int | str) -> int:
    contact_id_str = str(contact_id or "").strip()
    if not contact_id_str.isdigit():
        return 0
    deleted, _ = BitrixContactSnapshot.objects.filter(bitrix_contact_id=int(contact_id_str)).delete()
    return deleted


def delete_company_snapshot(company_id: int | str) -> int:
    company_id_str = str(company_id or "").strip()
    if not company_id_str.isdigit():
        return 0
    deleted, _ = BitrixCompanySnapshot.objects.filter(bitrix_company_id=int(company_id_str)).delete()
    return deleted
