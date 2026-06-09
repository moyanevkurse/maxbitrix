from celery import shared_task
from django.conf import settings
import logging

from mailings.models import Template, Partner, Mailing
from mailings.services.bitrix_sync import sync_bitrix_snapshots
from mailings.services.telegram import send_template_to_chat, send_template_to_chat_id

logger = logging.getLogger(__name__)


@shared_task
def run_mailing(
    template_id: int,
    partner_ids: list | None = None,
    batch_id: str = '',
    user_id: int | None = None,
    recipient_chat_ids: list[str] | None = None,
):
    """
    Разослать шаблон по списку партнёров. Создаёт записи в истории по каждому.
    batch_id — UUID строка для группировки записей в истории.
    """
    template = Template.objects.filter(pk=template_id).first()
    if not template:
        return {'ok': False, 'error': 'Шаблон не найден'}

    partner_ids = partner_ids or []
    recipient_chat_ids = [str(x).strip() for x in (recipient_chat_ids or []) if str(x).strip()]
    partners = Partner.objects.filter(pk__in=partner_ids, is_active=True)
    media_root = str(settings.MEDIA_ROOT)
    batch_uuid = batch_id
    sent_by_id = user_id

    success_count = 0
    error_count = 0

    for partner in partners:
        success, error_message = send_template_to_chat(template, partner, media_root)
        Mailing.objects.create(
            template=template,
            partner=partner,
            batch_id=batch_uuid,
            status=Mailing.STATUS_SUCCESS if success else Mailing.STATUS_ERROR,
            error_message=error_message or '',
            sent_by_id=sent_by_id,
        )
        if success:
            success_count += 1
            logger.info(
                'Mailing success: batch=%s template=%s partner_id=%s chat_id=%s',
                batch_uuid,
                template.pk,
                partner.pk,
                partner.chat_id,
            )
        else:
            error_count += 1
            logger.warning(
                'Mailing error: batch=%s template=%s partner_id=%s chat_id=%s error=%s',
                batch_uuid,
                template.pk,
                partner.pk,
                partner.chat_id,
                error_message or 'unknown error',
            )

    # Отправка по прямым chat_id из snapshot-пайплайна (без обязательного Partner).
    if recipient_chat_ids:
        partner_chat_ids = set(partners.values_list('chat_id', flat=True))
        for chat_id in recipient_chat_ids:
            if chat_id in partner_chat_ids:
                continue
            success, error_message = send_template_to_chat_id(template, chat_id, media_root)
            Mailing.objects.create(
                template=template,
                partner=None,
                recipient_chat_id=chat_id,
                batch_id=batch_uuid,
                status=Mailing.STATUS_SUCCESS if success else Mailing.STATUS_ERROR,
                error_message=error_message or '',
                sent_by_id=sent_by_id,
            )
            if success:
                success_count += 1
                logger.info(
                    'Mailing success (snapshot): batch=%s template=%s chat_id=%s',
                    batch_uuid,
                    template.pk,
                    chat_id,
                )
            else:
                error_count += 1
                logger.warning(
                    'Mailing error (snapshot): batch=%s template=%s chat_id=%s error=%s',
                    batch_uuid,
                    template.pk,
                    chat_id,
                    error_message or 'unknown error',
                )

    result = {
        'ok': True,
        'batch_id': batch_uuid,
        'success': success_count,
        'error': error_count,
    }
    logger.info('Mailing batch result: %s', result)
    return result


@shared_task
def sync_bitrix_snapshots_task(date_modify_from: str | None = None):
    """
    Синхронизация контактов и компаний из Bitrix в локальные snapshot-модели.
    date_modify_from: ISO datetime строка для инкрементального обновления.
    """
    stats = sync_bitrix_snapshots(date_modify_from=date_modify_from)
    result = {
        "ok": True,
        "contacts_total": stats.contacts_total,
        "contacts_saved": stats.contacts_saved,
        "companies_total": stats.companies_total,
        "companies_saved": stats.companies_saved,
    }
    logger.info("Bitrix snapshots task finished: %s", result)
    return result
