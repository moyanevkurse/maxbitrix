"""
Клиент Bitrix24 REST API (исходящий вебхук).
Экспорт партнёров в контакты CRM, приём входящих вебхуков — в views.
"""
import logging
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)


def get_webhook_url():
    from django.conf import settings
    url = getattr(settings, 'BITRIX_WEBHOOK_URL', '').strip()
    if not url:
        raise ValueError('BITRIX_WEBHOOK_URL не задан в настройках (или в .env)')
    return url.rstrip('/')


def _flatten_params(params: dict, prefix: str = '') -> dict:
    """Вложенный dict/list в плоский query для Bitrix REST: fields[IM][0][VALUE]=..."""
    out: dict = {}
    for k, v in params.items():
        key = f'{prefix}[{k}]' if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_params(v, key))
        elif isinstance(v, list):
            for idx, item in enumerate(v):
                item_key = f'{key}[{idx}]'
                if isinstance(item, dict):
                    out.update(_flatten_params(item, item_key))
                elif item is not None:
                    out[item_key] = item
        elif v is not None:
            out[key] = v
    return out


def call_bitrix(method: str, params: dict | None = None) -> dict:
    """
    Вызов метода REST API Bitrix24 (POST к вебхуку, параметры в query).
    method — например 'crm.contact.add'
    params — аргументы метода (fields и т.д.), передаются в query.
    """
    base = get_webhook_url()
    url = urljoin(base + '/', method)
    params = params or {}
    flat = _flatten_params(params)
    from django.conf import settings

    timeout_sec = int(getattr(settings, 'BITRIX_REQUEST_TIMEOUT_SEC', 30) or 30)
    resp = requests.post(url, params=flat, timeout=timeout_sec)
    resp.raise_for_status()
    data = resp.json()
    if data.get('error'):
        raise RuntimeError(f"Bitrix API error: {data.get('error_description', data.get('error'))}")
    return data


def get_user(user_id: str | int) -> dict | None:
    """Пользователь Bitrix24 (ответственный, сотрудник)."""
    try:
        result = call_bitrix('user.get', {'ID': str(user_id)})
        payload = result.get('result')
        if isinstance(payload, list):
            return payload[0] if payload else None
        if isinstance(payload, dict):
            return payload
        return None
    except Exception as e:
        logger.warning('Bitrix user.get failed: %s', e)
        return None


def find_employee_contact_by_bitrix_user_id(user_id: str | int) -> dict | None:
    """
    Контакт-сотрудник, привязанный к пользователю Bitrix (по UF или TYPE).
    """
    from django.conf import settings

    uid = str(user_id or '').strip()
    if not uid.isdigit():
        return None

    uf_link = str(getattr(settings, 'BITRIX_UF_EMPLOYEE_BITRIX_USER_ID', '') or '').strip()
    uf_max = str(getattr(settings, 'BITRIX_UF_EMPLOYEE_MAX_USER_ID', '') or '').strip()
    select = ['ID', 'NAME', 'LAST_NAME', 'PHONE', 'IM', 'ASSIGNED_BY_ID']
    if uf_link:
        select.append(uf_link)
    if uf_max:
        select.append(uf_max)

    if uf_link:
        try:
            result = call_bitrix('crm.contact.list', {
                'filter': {uf_link: uid},
                'select': select,
            })
            items = result.get('result') or []
            if items:
                return items[0]
        except Exception as e:
            logger.warning('Bitrix crm.contact.list (employee by user id) failed: %s', e)

    return None


def get_contact(contact_id: str | int) -> dict | None:
    """
    Получить контакт по ID. В ответе есть и пользовательские поля (UF_CRM_...),
    в т.ч. Telegram ID, TG Template, TG Send — под своими кодами.
    """
    try:
        result = call_bitrix('crm.contact.get', {'id': str(contact_id)})
        return result.get('result')
    except Exception as e:
        logger.warning('Bitrix crm.contact.get failed: %s', e)
        return None


def get_deal(deal_id: str | int) -> dict | None:
    """
    Получить сделку по ID. Возвращает dict с полями (TITLE, STAGE_ID, CONTACT_ID и т.д.) или None при ошибке.
    """
    try:
        result = call_bitrix('crm.deal.get', {'id': str(deal_id)})
        return result.get('result')
    except Exception as e:
        logger.warning('Bitrix crm.deal.get failed: %s', e)
        return None


def _get_uf_telegram_id():
    from django.conf import settings
    return getattr(settings, 'BITRIX_UF_TELEGRAM_ID', 'UF_CRM_1770617684')


def find_contact_by_name(name: str) -> dict | None:
    """
    Найти контакт в Битрикс24 по названию (поле NAME). Возвращает первый найденный или None.
    """
    if not (name or str(name).strip()):
        return None
    search_name = str(name).strip()[:255]
    try:
        result = call_bitrix('crm.contact.list', {
            'filter': {'NAME': search_name},
            'select': ['ID', 'NAME', 'COMMENTS'],
        })
        items = result.get('result') or []
        return items[0] if items else None
    except Exception as e:
        logger.warning('Bitrix crm.contact.list (by name) failed: %s', e)
        return None


def find_contact_by_telegram_id(telegram_id: str) -> dict | None:
    """
    Найти контакт в Битрикс24 по полю «Telegram ID» (UF_CRM_...).
    Возвращает первый найденный контакт или None.
    """
    if not (telegram_id or str(telegram_id).strip()):
        return None
    uf_field = _get_uf_telegram_id()
    try:
        result = call_bitrix('crm.contact.list', {
            'filter': {uf_field: str(telegram_id).strip()},
            'select': ['ID', 'NAME', 'COMMENTS', uf_field],
        })
        items = result.get('result') or []
        return items[0] if items else None
    except Exception as e:
        logger.warning('Bitrix crm.contact.list (by telegram_id) failed: %s', e)
        return None


def update_contact(contact_id: str | int, fields: dict) -> tuple[bool, str]:
    """Обновить контакт в Битрикс24. Возвращает (успех, текст ошибки)."""
    try:
        call_bitrix('crm.contact.update', {'id': str(contact_id), 'fields': fields})
        return True, ''
    except requests.HTTPError as e:
        detail = ''
        if e.response is not None:
            try:
                detail = e.response.json()
            except ValueError:
                detail = (e.response.text or '')[:500]
        logger.warning(
            'Bitrix crm.contact.update failed: contact_id=%s fields_keys=%s detail=%s',
            contact_id,
            list(fields.keys()),
            detail,
        )
        return False, str(detail or e)
    except Exception as e:
        logger.warning('Bitrix crm.contact.update failed: contact_id=%s error=%s', contact_id, e)
        return False, str(e)


def update_company(company_id: str | int, fields: dict) -> tuple[bool, str]:
    """Обновить компанию в Битрикс24. Возвращает (успех, текст ошибки)."""
    try:
        call_bitrix('crm.company.update', {'id': str(company_id), 'fields': fields})
        return True, ''
    except requests.HTTPError as e:
        detail = ''
        if e.response is not None:
            try:
                detail = e.response.json()
            except ValueError:
                detail = (e.response.text or '')[:500]
        logger.warning(
            'Bitrix crm.company.update failed: company_id=%s fields_keys=%s detail=%s',
            company_id,
            list(fields.keys()),
            detail,
        )
        return False, str(detail or e)
    except Exception as e:
        logger.warning('Bitrix crm.company.update failed: company_id=%s error=%s', company_id, e)
        return False, str(e)


def add_contact(
    name: str,
    comments: str = '',
    last_name: str = '',
    telegram_id: str | None = None,
) -> int:
    """
    Создать контакт в CRM Bitrix24. Возвращает ID созданного контакта.
    Если передан telegram_id — записывается в пользовательское поле «Telegram ID» (связь для рассылок по сделкам).
    """
    fields = {
        'NAME': name or 'Без имени',
        'LAST_NAME': last_name,
        'COMMENTS': comments,
    }
    if telegram_id is not None and str(telegram_id).strip():
        fields[_get_uf_telegram_id()] = str(telegram_id).strip()
    result = call_bitrix('crm.contact.add', {'fields': fields})
    return int(result.get('result', 0))
