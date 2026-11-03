"""
Входящий вебхук от Bitrix24 — приём событий.
"""
import json
import logging
import re
from datetime import timedelta
from urllib.parse import parse_qs, unquote_plus

from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


def _deal_send_tg_enabled(deal: dict, settings) -> bool:
    """True, если по сделке нужно отправить в TG: стадия совпадает или поле «шлём в TG» = true."""
    stage = (deal.get('STAGE_ID') or '').strip()
    if stage == getattr(settings, 'BITRIX_STAGE_MAILING', 'PREPAYMENT_INVOICE'):
        return True
    uf_send = getattr(settings, 'BITRIX_UF_DEAL_SEND_TG', 'UF_CRM_1771569433076')
    val = deal.get(uf_send)
    return val in (True, 1, '1', 'Y', 'y', 'true', 'yes')


def _maybe_send_bitrix_deal_mailing(deal: dict, contact: dict) -> None:
    """
    Если сделка в стадии рассылки (PREPAYMENT_INVOICE) или у сделки включено поле «шлём в TG»,
    взять из контакта Telegram ID и шаблон, найти/создать Partner и Template, поставить рассылку в очередь.
    """
    from django.conf import settings
    from mailings.models import Partner, Template, Mailing
    from mailings.tasks import run_mailing

    if not _deal_send_tg_enabled(deal, settings):
        return

    uf_tg_id = getattr(settings, 'BITRIX_UF_TELEGRAM_ID', 'UF_CRM_1770617684')
    uf_tpl = getattr(settings, 'BITRIX_UF_TG_TEMPLATE', 'UF_CRM_1770624168')
    telegram_id = (contact.get(uf_tg_id) or '').strip()
    template_name = (contact.get(uf_tpl) or '').strip()
    if not telegram_id or not template_name:
        print('  -> Рассылка: нет Telegram ID или шаблона в контакте, пропуск')
        return

    partner, created = Partner.objects.get_or_create(
        chat_id=telegram_id,
        defaults={
            'chat_type': Partner.TYPE_CHANNEL,
            'title': (contact.get('NAME') or telegram_id)[:255],
            'is_active': True,
        },
    )
    template = Template.objects.filter(name__iexact=template_name).first()
    if not template:
        print(f'  -> Рассылка: шаблон с названием "{template_name}" не найден в Django')
        return

    batch_id = str(Mailing.create_batch_id())
    run_mailing.delay(
        template_id=template.pk,
        partner_ids=[partner.pk],
        batch_id=batch_id,
        user_id=None,
    )
    print(f'  -> Рассылка поставлена в очередь: шаблон "{template.name}" → {partner} (batch {batch_id[:8]}…)')


def _parse_bitrix_webhook_body(body: bytes) -> dict:
    """Битрикс шлёт POST как application/x-www-form-urlencoded. Разбираем в плоский dict."""
    if not body:
        return {}
    text = body.decode('utf-8', errors='replace')
    if text.strip().startswith('{'):
        return json.loads(text)
    # form: event=ONCRMDEALUPDATE&data[FIELDS][ID]=2&auth[domain]=...
    raw = parse_qs(text, keep_blank_values=True)
    # убираем списки из одного элемента для удобства
    data = {}
    for k, v in raw.items():
        key = unquote_plus(k)
        val = v[0] if len(v) == 1 else v
        if isinstance(val, str):
            val = unquote_plus(val)
        data[key] = val
    return data


def _is_true(value) -> bool:
    return value in (True, 1, '1', 'Y', 'y', 'true', 'yes', 'on')


def _extract_contact_id(data: dict) -> str:
    for key in ('contact_id', 'id', 'data[FIELDS][ID]', 'CONTACT_ID'):
        val = data.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ''


def _extract_entity_id(data: dict) -> str:
    for key in ('data[FIELDS][ID]', 'id', 'ID'):
        val = data.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ''


def _extract_requisite_owner(data: dict) -> tuple[str, str]:
    """
    Для ONCRMREQUISITE* в FIELDS приходит владелец реквизита (контакт/компания), не ID реквизита.
    """
    entity_type = ''
    entity_id = ''
    for key in ('data[FIELDS][ENTITY_TYPE_ID]', 'ENTITY_TYPE_ID'):
        val = data.get(key)
        if val is not None and str(val).strip():
            entity_type = str(val).strip()
            break
    for key in ('data[FIELDS][ENTITY_ID]', 'ENTITY_ID'):
        val = data.get(key)
        if val is not None and str(val).strip():
            entity_id = str(val).strip()
            break
    return entity_type, entity_id


def _sync_snapshot_from_bitrix_event(*, event: str, data: dict) -> None:
    """Обновляет snapshot по событию Bitrix (контакт, компания, реквизит)."""
    from mailings.services.bitrix_sync import (
        delete_company_snapshot,
        delete_contact_snapshot,
        sync_company_by_id,
        sync_contact_by_id,
    )

    event_upper = (event or '').upper()

    if 'ONCRMREQUISITE' in event_upper:
        entity_type, entity_id = _extract_requisite_owner(data)
        if not entity_id.isdigit():
            print(f'  -> Requisite event: owner entity_id missing ({entity_type=}, {entity_id=})')
            return
        if entity_type == '3':
            print(f'  -> Requisite -> contact snapshot sync: contact_id={entity_id}')
            if event_upper.endswith('DELETE'):
                print(f'  -> Requisite delete ignored for contact (sync on contact update)')
                return
            ok = sync_contact_by_id(entity_id)
            print(f'  -> Contact snapshot sync (via requisite): id={entity_id}, ok={ok}')
            return
        if entity_type == '4':
            print(f'  -> Requisite -> company snapshot sync: company_id={entity_id}')
            if event_upper.endswith('DELETE'):
                return
            ok = sync_company_by_id(entity_id)
            print(f'  -> Company snapshot sync (via requisite): id={entity_id}, ok={ok}')
            return
        print(f'  -> Requisite event ignored: ENTITY_TYPE_ID={entity_type}')
        return

    entity_id = _extract_entity_id(data)
    if not entity_id:
        return

    if 'ONCRMCONTACT' in event_upper:
        print(f'  -> Детали контакта: crm.contact.get id={entity_id}')
        if event_upper.endswith('DELETE'):
            deleted = delete_contact_snapshot(entity_id)
            logger.info('Bitrix contact snapshot deleted by webhook: id=%s deleted=%s', entity_id, deleted)
            print(f'  -> Contact snapshot deleted: id={entity_id}, deleted={deleted}')
        else:
            ok = sync_contact_by_id(entity_id)
            logger.info('Bitrix contact snapshot synced by webhook: id=%s ok=%s', entity_id, ok)
            print(f'  -> Contact snapshot sync: id={entity_id}, ok={ok}')
        return

    if 'ONCRMCOMPANY' in event_upper:
        print(f'  -> Детали компании: crm.company.get id={entity_id}')
        if event_upper.endswith('DELETE'):
            deleted = delete_company_snapshot(entity_id)
            logger.info('Bitrix company snapshot deleted by webhook: id=%s deleted=%s', entity_id, deleted)
            print(f'  -> Company snapshot deleted: id={entity_id}, deleted={deleted}')
        else:
            ok = sync_company_by_id(entity_id)
            logger.info('Bitrix company snapshot synced by webhook: id=%s ok=%s', entity_id, ok)
            print(f'  -> Company snapshot sync: id={entity_id}, ok={ok}')


def _extract_deal_id(data: dict) -> str:
    """
    Пытается извлечь ID сделки из payload Битрикса:
    - data[FIELDS][ID] для ONCRMDEAL*
    - document_id[2] вида DEAL_5
    """
    val = data.get('data[FIELDS][ID]')
    if val and str(val).strip().isdigit():
        return str(val).strip()
    doc = str(data.get('document_id[2]') or '').strip()
    if doc.upper().startswith('DEAL_'):
        candidate = doc.split('_', 1)[1]
        if candidate.isdigit():
            return candidate
    return ''


def _extract_chat_id_from_id_max(id_max: str) -> str:
    """
    Извлекает chat_id из значения вида:
    imol|rest|1|max:-72853854483952|0
    """
    raw = str(id_max or '').strip()
    if not raw:
        return ''
    parts = raw.split('|')
    for part in parts:
        part = part.strip()
        if part.startswith('max:'):
            return part
    # Формат лички/канала может приходить как:
    # imol|max|1|206349296|23  -> внешний ID в 4-й части
    if len(parts) >= 4 and parts[0] == 'imol' and parts[1] in ('max', 'rest'):
        external_id = str(parts[3]).strip()
        if external_id:
            return f'max:{external_id}'
    return ''


def _split_csv(raw) -> list[str]:
    if isinstance(raw, list):
        values = raw
    else:
        values = str(raw or '').split(',')
    return [str(x).strip() for x in values if str(x).strip()]


def _extract_multi_values(data: dict, *keys: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key not in data:
            continue
        for value in _split_csv(data.get(key)):
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
    return result


def _normalize_max_target(
    value: str,
    *,
    allow_positive_numeric: bool = False,
    allow_negative_numeric: bool = False,
) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    match_group = re.fullmatch(r'max:g:(-?\d+)', text)
    if match_group:
        return f'max:{match_group.group(1)}'
    if text.startswith('max:'):
        candidate = text.split(':', 1)[1].strip()
        if re.fullmatch(r'-?\d+', candidate):
            return f'max:{candidate}'
        return ''
    if re.fullmatch(r'-?\d+', text):
        if text.startswith('-') and allow_negative_numeric:
            return f'max:{text}'
        if not text.startswith('-') and allow_positive_numeric:
            return f'max:{text}'
    return ''


def _extract_max_targets_from_im_value(raw_value: str) -> list[tuple[str, str]]:
    raw = str(raw_value or '').strip()
    if not raw:
        return []

    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(target: str, source: str) -> None:
        if target not in seen:
            results.append((target, source))
            seen.add(target)

    match_group_user = re.search(r'max:g:([^:|]+):u:(\d+)', raw)
    if match_group_user:
        group_id = str(match_group_user.group(1)).strip()
        user_id = str(match_group_user.group(2)).strip()
        if group_id and re.fullmatch(r'-?\d+', group_id):
            _add(f'max:{group_id}', 'IMOL max:g -> group_id')
        if user_id and re.fullmatch(r'-?\d+', user_id):
            _add(f'max:{user_id}', 'IMOL max:g -> user_id')

    for match in re.finditer(r'max:g:([^:|]+)', raw):
        group_id = str(match.group(1)).strip()
        if group_id and re.fullmatch(r'-?\d+', group_id):
            _add(f'max:{group_id}', 'IMOL max:g')

    match_dm = re.search(r'max:dm:(\d+)', raw)
    if match_dm:
        _add(f"max:{match_dm.group(1)}", 'IMOL max:dm')

    for legacy_id in re.findall(r'max:(-?\d+)(?![:\d])', raw):
        _add(f'max:{legacy_id}', 'IMOL max legacy')

    return results


def _contact_display_name(snapshot) -> str:
    return snapshot.full_name or f'Контакт #{snapshot.bitrix_contact_id}'


def _company_display_name(snapshot) -> str:
    return snapshot.title or f'Компания #{snapshot.bitrix_company_id}'


def _extract_contact_max_targets(snapshot) -> tuple[dict[str, str], str]:
    targets: dict[str, str] = {}
    raw = snapshot.raw_data or {}

    normalized_max_chat_id = _normalize_max_target(
        getattr(snapshot, 'max_chat_id', ''),
        allow_positive_numeric=True,
        allow_negative_numeric=True,
    )
    if normalized_max_chat_id:
        targets[normalized_max_chat_id] = 'max_chat_id'

    im_values = raw.get('IM') or []
    if isinstance(im_values, list):
        for item in im_values:
            if not isinstance(item, dict):
                continue
            for target, source in _extract_max_targets_from_im_value(item.get('VALUE') or ''):
                targets.setdefault(target, source)

    if targets:
        return targets, ''

    if getattr(snapshot, 'max_chat_id', ''):
        return {}, 'Есть max_chat_id, но target не распознан'
    if im_values:
        return {}, 'Есть IM/IMOL, но нет распознаваемого MAX target'
    return {}, 'Нет MAX-информации (нет max_chat_id и IMOL max:*)'


def _company_max_chat_raw_keys() -> tuple[str, ...]:
    from django.conf import settings

    company_uf = str(getattr(settings, 'BITRIX_UF_COMPANY_MAX_CHAT_ID', 'UF_CRM_1779436775') or 'UF_CRM_1779436775')
    keys = (
        company_uf,
        'UF_CRM_CHATAPP_DIALOG',
        'UF_CRM_1774872874',
        'UF_CRM_1774872975',
        'UF_CRM_1774873038',
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return tuple(ordered)


def _extract_company_direct_max_targets(snapshot) -> dict[str, str]:
    targets: dict[str, str] = {}

    normalized_snapshot_chat = _normalize_max_target(
        getattr(snapshot, 'max_chat_id', ''),
        allow_positive_numeric=True,
        allow_negative_numeric=True,
    )
    if normalized_snapshot_chat:
        targets[normalized_snapshot_chat] = 'max_chat_id (компания)'

    raw = snapshot.raw_data or {}
    for key in _company_max_chat_raw_keys():
        value = raw.get(key)
        target = _normalize_max_target(value, allow_positive_numeric=True, allow_negative_numeric=True)
        if target:
            targets.setdefault(target, f'Компания: {key}')
    return targets


def _is_max_group_target(chat_id: str) -> bool:
    raw = str(chat_id or '').strip()
    if raw.startswith('max:'):
        raw = raw.split(':', 1)[1].strip()
    return raw.startswith('-')


MAILING_CHANNEL_MAX = 'max'
MAILING_CHANNEL_TELEGRAM = 'telegram'
MAILING_CHANNELS = {MAILING_CHANNEL_MAX, MAILING_CHANNEL_TELEGRAM}


def _normalize_mailing_channels(channels, *, default_to_max: bool = True) -> set[str]:
    """Returns only known delivery channels.

    Existing Bitrix integrations do not pass a channel at all.  They keep their
    historical MAX-only behaviour through ``default_to_max``.  The admin UI
    passes ``default_to_max=False`` and requires an explicit choice.
    """
    selected = {str(channel).strip().lower() for channel in (channels or [])}
    selected &= MAILING_CHANNELS
    if not selected and default_to_max:
        return {MAILING_CHANNEL_MAX}
    return selected


def _normalize_telegram_group_target(value: str) -> str:
    """Telegram group/supergroup ids are negative numeric values.

    Do not accept positive Telegram user ids here: this sender is deliberately
    limited to groups and must never turn a snapshot value into a direct message.
    """
    target = str(value or '').strip()
    return target if re.fullmatch(r'-\d+', target) else ''


def _extract_contact_telegram_group_targets(snapshot) -> tuple[dict[str, str], str]:
    target = _normalize_telegram_group_target(getattr(snapshot, 'telegram_group_id', ''))
    if target:
        return {target: 'telegram_group_id'}, ''
    if getattr(snapshot, 'telegram_group_id', ''):
        return {}, 'Есть telegram_group_id, но это не ID Telegram-группы'
    return {}, 'Нет telegram_group_id'


def _extract_company_direct_telegram_group_targets(snapshot) -> dict[str, str]:
    target = _normalize_telegram_group_target(getattr(snapshot, 'telegram_group_id', ''))
    return {target: 'telegram_group_id (компания)'} if target else {}


def _is_target_allowed_for_chat_kinds(chat_id: str, chat_kinds: list[str]) -> bool:
    """Treat every selected Telegram target as a group; retain MAX's old rule."""
    if not chat_kinds:
        return True
    if _normalize_telegram_group_target(chat_id):
        return 'group' in chat_kinds
    is_group_target = _is_max_group_target(chat_id)
    return ('group' in chat_kinds and is_group_target) or ('private' in chat_kinds and not is_group_target)


def _filter_snapshots_by_city(qs, city: str):
    """Подстрока в address, без учёта регистра (icontains / ILIKE)."""
    needle = str(city or '').strip()
    if not needle:
        return qs
    return qs.filter(address__icontains=needle)


def _collect_recipients_from_filters_data(
    *,
    group_ids: list[int],
    partner_ids: list[int],
    city_filter: str,
    sleep_status: str,
    company_types: list[str],
    potentials: list[str],
    chat_kinds: list[str],
    contact_job_titles: list[str] | None = None,
    channels: list[str] | set[str] | None = None,
) -> dict:
    from mailings.models import BitrixCompanySnapshot, BitrixContactSnapshot, Partner

    partner_pks = set(partner_ids or [])
    recipient_chat_ids: set[str] = set()
    included: list[dict] = []
    excluded: list[dict] = []
    contact_job_titles = [str(v).strip() for v in (contact_job_titles or []) if str(v).strip()]
    selected_channels = _normalize_mailing_channels(channels)

    if group_ids:
        partner_pks.update(
            Partner.objects.filter(group_id__in=group_ids, is_active=True).values_list('pk', flat=True)
        )

    # Chat kind checkboxes тоже должны уметь сами запускать подбор из snapshot.
    # Иначе сценарий "покажи все личные" / "покажи все группы" возвращает пусто.
    city_needle = str(city_filter or '').strip()
    # Для Telegram отдельный "все группы" источник — сохранённые group ID.
    # MAX без фильтров сохраняет прежнее поведение и сам по себе не разворачивает
    # всю базу snapshot.
    if (
        city_needle
        or sleep_status
        or company_types
        or potentials
        or chat_kinds
        or contact_job_titles
        or MAILING_CHANNEL_TELEGRAM in selected_channels
    ):
        now = timezone.now()
        threshold = now - timedelta(days=30)

        contact_qs = BitrixContactSnapshot.objects.all()
        company_qs = BitrixCompanySnapshot.objects.all()

        if city_needle:
            contact_qs = _filter_snapshots_by_city(contact_qs, city_needle)
            company_qs = _filter_snapshots_by_city(company_qs, city_needle)

        if sleep_status == 'awake':
            contact_qs = contact_qs.filter(last_sale_at__isnull=False, last_sale_at__gte=threshold)
            company_qs = company_qs.filter(last_sale_at__isnull=False, last_sale_at__gte=threshold)
        elif sleep_status == 'sleeping':
            contact_qs = contact_qs.filter(Q(last_sale_at__isnull=True) | Q(last_sale_at__lt=threshold))
            company_qs = company_qs.filter(Q(last_sale_at__isnull=True) | Q(last_sale_at__lt=threshold))

        if company_types:
            company_qs = company_qs.filter(segment_value_id__in=company_types)

        if potentials:
            company_qs = company_qs.filter(potential_value_id__in=potentials)

        if contact_job_titles:
            contact_qs = contact_qs.filter(job_title__in=contact_job_titles)

        all_contacts = list(
            BitrixContactSnapshot.objects.all().only(
                'bitrix_contact_id',
                'full_name',
                'max_chat_id',
                'telegram_group_id',
                'raw_data',
            )
        )
        contacts_by_company_id: dict[str, list] = {}
        for contact in all_contacts:
            raw = contact.raw_data or {}
            company_id = str(raw.get('COMPANY_ID') or '').strip()
            if not company_id:
                continue
            contacts_by_company_id.setdefault(company_id, []).append(contact)

        # Контакты как самостоятельный источник включаем только когда нет
        # фильтров компании. Иначе выборка должна сужаться по компаниям (AND),
        # без "подмешивания" всех контактов по городу/статусу.
        # Фильтр типа контакта всегда включает прямые контакты.
        include_direct_contacts = (not bool(company_types or potentials)) or bool(contact_job_titles)
        if include_direct_contacts:
            for contact in contact_qs.only(
                'bitrix_contact_id', 'full_name', 'max_chat_id', 'telegram_group_id', 'raw_data'
            ):
                targets: dict[str, str] = {}
                reasons: list[str] = []
                if MAILING_CHANNEL_MAX in selected_channels:
                    max_targets, reason = _extract_contact_max_targets(contact)
                    targets.update(max_targets)
                    if not max_targets:
                        reasons.append(reason)
                if MAILING_CHANNEL_TELEGRAM in selected_channels:
                    telegram_targets, reason = _extract_contact_telegram_group_targets(contact)
                    targets.update(telegram_targets)
                    if not telegram_targets:
                        reasons.append(reason)
                if targets:
                    for target, source in targets.items():
                        recipient_chat_ids.add(target)
                        included.append(
                            {
                                'kind': 'contact',
                                'entity_id': str(contact.bitrix_contact_id),
                                'name': _contact_display_name(contact),
                                'target': target,
                                'source': source,
                            }
                        )
                else:
                    excluded.append(
                        {
                            'kind': 'contact',
                            'entity_id': str(contact.bitrix_contact_id),
                            'name': _contact_display_name(contact),
                            'reason': '; '.join(reasons) or 'Нет подходящего адресата',
                        }
                    )

        # Если выбран только тип контакта — компании не подмешиваем.
        include_companies = not (
            bool(contact_job_titles) and not bool(company_types or potentials or city_needle or sleep_status)
        )
        if include_companies:
            for company in company_qs.only(
                'bitrix_company_id', 'title', 'max_chat_id', 'telegram_group_id', 'raw_data'
            ):
                company_targets: dict[str, str] = {}
                if MAILING_CHANNEL_MAX in selected_channels:
                    company_targets.update(_extract_company_direct_max_targets(company))
                if MAILING_CHANNEL_TELEGRAM in selected_channels:
                    company_targets.update(_extract_company_direct_telegram_group_targets(company))
                linked_contacts = contacts_by_company_id.get(str(company.bitrix_company_id), [])
                linked_targets: dict[str, str] = {}
                for contact in linked_contacts:
                    targets: dict[str, str] = {}
                    if MAILING_CHANNEL_MAX in selected_channels:
                        max_targets, _ = _extract_contact_max_targets(contact)
                        targets.update(max_targets)
                    if MAILING_CHANNEL_TELEGRAM in selected_channels:
                        telegram_targets, _ = _extract_contact_telegram_group_targets(contact)
                        targets.update(telegram_targets)
                    for target, source in targets.items():
                        linked_targets.setdefault(target, f'{source}; связанный контакт {_contact_display_name(contact)}')

                if company_targets or linked_targets:
                    merged_targets = {**company_targets, **linked_targets}
                    for target, source in merged_targets.items():
                        recipient_chat_ids.add(target)
                        included.append(
                            {
                                'kind': 'company',
                                'entity_id': str(company.bitrix_company_id),
                                'name': _company_display_name(company),
                                'target': target,
                                'source': source,
                            }
                        )
                else:
                    excluded.append(
                        {
                            'kind': 'company',
                            'entity_id': str(company.bitrix_company_id),
                            'name': _company_display_name(company),
                            'reason': 'Нет адресата выбранного канала в компании и у связанных контактов',
                        }
                    )

    # MAX может отправляться всем историческим типам Partner. Telegram же в
    # этой странице принципиально оставляем только для Partner-групп.
    if partner_pks:
        allowed_partner_qs = Partner.objects.filter(pk__in=partner_pks, is_active=True)
        channel_filter = Q()
        if MAILING_CHANNEL_MAX in selected_channels:
            channel_filter |= Q(chat_id__startswith='max:')
        if MAILING_CHANNEL_TELEGRAM in selected_channels:
            channel_filter |= ~Q(chat_id__startswith='max:') & Q(chat_type=Partner.TYPE_GROUP)
        partner_pks = set(allowed_partner_qs.filter(channel_filter).values_list('pk', flat=True))

    if chat_kinds:
        allowed_types = set()
        if 'private' in chat_kinds:
            allowed_types.add(Partner.TYPE_PRIVATE)
        if 'group' in chat_kinds:
            allowed_types.update({Partner.TYPE_GROUP, Partner.TYPE_CHANNEL})

        if allowed_types and partner_pks:
            partner_pks = set(
                Partner.objects.filter(
                    pk__in=partner_pks,
                    is_active=True,
                    chat_type__in=list(allowed_types),
                ).values_list('pk', flat=True)
            )

        filtered_ids = set()
        allowed_chat_id_kinds: set[str] = set()
        for chat_id in recipient_chat_ids:
            if _is_target_allowed_for_chat_kinds(chat_id, chat_kinds):
                filtered_ids.add(chat_id)
                allowed_chat_id_kinds.add(chat_id)
        recipient_chat_ids = filtered_ids

        if allowed_chat_id_kinds:
            included = [item for item in included if item.get('target') in allowed_chat_id_kinds]
        elif chat_kinds:
            included = []

    deduped_included: list[dict] = []
    seen_included: set[tuple[str, str, str]] = set()
    for item in included:
        marker = (item.get('kind', ''), item.get('entity_id', ''), item.get('target', ''))
        if marker in seen_included:
            continue
        seen_included.add(marker)
        deduped_included.append(item)

    deduped_excluded: list[dict] = []
    seen_excluded: set[tuple[str, str, str]] = set()
    for item in excluded:
        marker = (item.get('kind', ''), item.get('entity_id', ''), item.get('reason', ''))
        if marker in seen_excluded:
            continue
        seen_excluded.add(marker)
        deduped_excluded.append(item)

    return {
        'partner_pks': partner_pks,
        'recipient_chat_ids': recipient_chat_ids,
        'included': deduped_included,
        'excluded': deduped_excluded,
        'channels': sorted(selected_channels),
    }


def _collect_recipients_from_filters(
    *,
    group_ids: list[int],
    partner_ids: list[int],
    city_filter: str,
    sleep_status: str,
    company_types: list[str],
    potentials: list[str],
    chat_kinds: list[str],
    contact_job_titles: list[str] | None = None,
    channels: list[str] | set[str] | None = None,
) -> tuple[set[int], set[str]]:
    data = _collect_recipients_from_filters_data(
        group_ids=group_ids,
        partner_ids=partner_ids,
        city_filter=city_filter,
        sleep_status=sleep_status,
        company_types=company_types,
        potentials=potentials,
        chat_kinds=chat_kinds,
        contact_job_titles=contact_job_titles,
        channels=channels,
    )
    return data['partner_pks'], data['recipient_chat_ids']


@csrf_exempt
@require_http_methods(['POST'])
def bitrix_contact_mailing_webhook(request):
    """
    Отдельный endpoint для запуска рассылки из карточки Контакта Bitrix.
    Ожидается contact_id (или data[FIELDS][ID]) и флаг отправки в поле контакта.
    """
    from mailings.models import Mailing, Partner, Template
    from mailings.tasks import run_mailing

    try:
        data = _parse_bitrix_webhook_body(request.body or b'')
        # Роботы/БП часто передают аргументы в query-string.
        data.update(request.GET.dict())
        logger.info('Bitrix contact mailing webhook received: %s', data)

        # Режим без дополнительных запросов в Bitrix:
        # передаём всё нужное прямо в webhook (chat_id, template, send, title).
        chat_id = str(data.get('chat_id') or data.get('telegram_id') or '').strip()
        if not chat_id:
            chat_id = _extract_chat_id_from_id_max(data.get('id_max') or '')
        template_name = str(data.get('template') or data.get('template_name') or '').strip()
        send_flag = data.get('send', 'Y')
        title = str(data.get('title') or data.get('name') or chat_id).strip()

        if not _is_true(send_flag):
            return JsonResponse({'ok': True, 'result': 'skip: send flag is disabled'})
        if not chat_id or not template_name:
            return JsonResponse({'ok': False, 'error': 'chat_id and template are required'}, status=400)

        partner_type = Partner.TYPE_PRIVATE
        if chat_id.startswith('max:'):
            partner_type = Partner.TYPE_GROUP
        partner, _ = Partner.objects.update_or_create(
            chat_id=chat_id,
            defaults={
                'chat_type': partner_type,
                'title': (title or chat_id)[:255],
                'is_active': True,
            },
        )

        template = Template.objects.filter(name__iexact=template_name).first()
        if not template:
            return JsonResponse({'ok': False, 'error': f'template not found: {template_name}'}, status=404)

        batch_id = str(Mailing.create_batch_id())
        run_mailing.delay(
            template_id=template.pk,
            partner_ids=[partner.pk],
            batch_id=batch_id,
            user_id=None,
        )

        return JsonResponse({'ok': True, 'batch_id': batch_id, 'partner_id': partner.pk, 'template': template.name})
    except Exception as exc:
        logger.exception('Bitrix contact mailing webhook error: %s', exc)
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


@csrf_exempt
@require_http_methods(['POST', 'GET'])
def bitrix_run_mailing_webhook(request):
    """
    Универсальный webhook запуска рассылки из Bitrix-роботов.
    Поддерживает те же фильтры, что и админка run-mailing.
    """
    from django.conf import settings
    from mailings.models import Mailing, Partner, Template
    from mailings.tasks import run_mailing

    try:
        data = _parse_bitrix_webhook_body(request.body or b'')
        data.update(request.GET.dict())
        data.update(request.POST.dict())
        logger.info('Bitrix run-mailing webhook received: %s', data)

        expected_secret = str(getattr(settings, 'BITRIX_MAILING_WEBHOOK_SECRET', '') or '').strip()
        if expected_secret:
            provided_secret = (
                request.headers.get('X-Webhook-Secret')
                or request.headers.get('X-Bitrix-Mailing-Secret')
                or str(data.get('secret') or '').strip()
            )
            if provided_secret != expected_secret:
                return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

        template_ref = str(data.get('template_id') or data.get('template') or data.get('template_name') or '').strip()
        if not template_ref:
            return JsonResponse({'ok': False, 'error': 'template_id or template is required'}, status=400)

        template = None
        if template_ref.isdigit():
            template = Template.objects.filter(pk=int(template_ref)).first()
        if not template:
            template = Template.objects.filter(name__iexact=template_ref).first()
        if not template:
            return JsonResponse({'ok': False, 'error': f'template not found: {template_ref}'}, status=404)

        group_ids = [int(x) for x in _extract_multi_values(data, 'group_ids', 'groups') if x.isdigit()]
        partner_ids = [int(x) for x in _extract_multi_values(data, 'partner_ids', 'partners') if x.isdigit()]
        recipient_chat_ids = _extract_multi_values(data, 'recipient_chat_ids', 'chat_ids')
        city_filter = str(data.get('city_filter') or data.get('city') or '').strip()
        if not city_filter and _is_true(data.get('city_moscow')):
            city_filter = 'москва'
        sleep_status = str(data.get('sleep_status') or '').strip().lower()
        company_types = _extract_multi_values(data, 'company_types')
        potentials = _extract_multi_values(data, 'potentials')
        chat_kinds = _extract_multi_values(data, 'chat_kinds')
        contact_job_titles = _extract_multi_values(data, 'contact_job_titles')
        source = str(data.get('source') or 'bitrix_robot').strip()
        request_id = str(data.get('request_id') or '').strip()

        filtered_partner_pks, filtered_chat_ids = _collect_recipients_from_filters(
            group_ids=group_ids,
            partner_ids=partner_ids,
            city_filter=city_filter,
            sleep_status=sleep_status,
            company_types=company_types,
            potentials=potentials,
            chat_kinds=chat_kinds,
            contact_job_titles=contact_job_titles,
        )

        if recipient_chat_ids:
            filtered_chat_ids.update(recipient_chat_ids)

        if not filtered_partner_pks and not filtered_chat_ids:
            return JsonResponse(
                {'ok': False, 'error': 'no recipients by filters/selection'},
                status=400,
            )

        partners = Partner.objects.filter(pk__in=filtered_partner_pks, is_active=True)
        batch_id = str(Mailing.create_batch_id())
        run_mailing.delay(
            template_id=template.pk,
            partner_ids=list(partners.values_list('pk', flat=True)),
            recipient_chat_ids=list(filtered_chat_ids),
            batch_id=batch_id,
            user_id=None,
        )
        logger.info(
            'Bitrix run-mailing queued: batch=%s source=%s request_id=%s template=%s partners=%s chat_ids=%s',
            batch_id,
            source,
            request_id,
            template.pk,
            len(filtered_partner_pks),
            len(filtered_chat_ids),
        )
        return JsonResponse(
            {
                'ok': True,
                'queued': True,
                'batch_id': batch_id,
                'template': template.name,
                'partners_count': len(filtered_partner_pks),
                'chat_ids_count': len(filtered_chat_ids),
            },
            status=202,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception('Bitrix run-mailing webhook error: %s', exc)
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


@csrf_exempt
@require_http_methods(['POST', 'GET'])
def bitrix_webhook(request):
    """
    Endpoint для входящего вебхука Битрикс24.
    Парсим тело (JSON или form-urlencoded), логируем и печатаем что пришло.
    """
    try:
        if request.method == 'POST':
            body = request.body
            data = _parse_bitrix_webhook_body(body) if body else {}
            logger.info('Bitrix webhook received: %s', data)
            print('[Bitrix webhook POST]', json.dumps(data, ensure_ascii=False, indent=2))
            # Кратко: что за событие и что можно достукать по API
            event = data.get('event', '')
            if event:
                print(f'  -> Событие: {event}')
            entity_id = _extract_entity_id(data)
            if entity_id:
                print(f'  -> ID сущности: {entity_id}')
                if 'ONCRMDEAL' in (event or ''):
                    from django.conf import settings
                    if getattr(settings, 'BITRIX_WEBHOOK_URL', '').strip():
                        from mailings.services.bitrix import get_deal, get_contact
                        deal = get_deal(entity_id)
                        if deal:
                            print('  -> Сделка:', json.dumps(deal, ensure_ascii=False, indent=4))
                            contact_id = (deal.get('CONTACT_ID') or deal.get('CONTACT_IDS') or '').strip()
                            if contact_id and contact_id.isdigit():
                                contact = get_contact(contact_id)
                                if contact:
                                    print('  -> Контакт (в т.ч. UF_* — Telegram ID, TG Template, TG Send):',
                                          json.dumps(contact, ensure_ascii=False, indent=4))
                                    # При переходе в стадию «расс» — отправить в Telegram по полям контакта
                                    _maybe_send_bitrix_deal_mailing(deal, contact)
                        else:
                            print('  -> Сделка: не удалось загрузить (нет прав или неверный URL)')
                    else:
                        print('  -> Детали сделки: задайте BITRIX_WEBHOOK_URL для автозапроса crm.deal.get')
                if any(
                    token in (event or '').upper()
                    for token in ('ONCRMCONTACT', 'ONCRMCOMPANY', 'ONCRMREQUISITE')
                ):
                    try:
                        _sync_snapshot_from_bitrix_event(event=event, data=data)
                    except Exception as sync_exc:
                        logger.warning('Bitrix snapshot webhook sync failed (event=%s): %s', event, sync_exc)
                        print(f'  -> Snapshot sync failed: {sync_exc}')
        else:
            logger.info('Bitrix webhook GET: %s', request.GET)
            print('[Bitrix webhook GET]', dict(request.GET))
    except Exception as e:
        logger.exception('Bitrix webhook parse error: %s', e)
        print('[Bitrix webhook ERROR]', e)
    return HttpResponse('ok', content_type='text/plain')
