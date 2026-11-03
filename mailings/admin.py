from django import forms
from datetime import timedelta
from django.contrib import admin
from django.contrib import messages
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import path
from django.urls import reverse
from django.utils import timezone
from .models import (
    Partner,
    PartnerGroup,
    Template,
    TemplateAttachment,
    Mailing,
    BitrixContactSnapshot,
    BitrixCompanySnapshot,
)
from .tasks import run_mailing
from .tasks import sync_bitrix_snapshots_task
from .validators import validate_message_length

PREVIEW_RECIPIENT_LIMIT = 200


def build_mailing_preview(
    *,
    partners,
    recipient_chat_ids: set[str],
    included: list[dict],
    excluded: list[dict],
    partners_total: int,
    limit: int = PREVIEW_RECIPIENT_LIMIT,
    checked_partner_ids: set[int] | None = None,
    checked_chat_ids: set[str] | None = None,
) -> dict:
    """Собирает предпросмотр с чекбоксами для выборочной тестовой отправки."""
    recipients: list[dict] = []
    seen_chat_ids: set[str] = set()
    truncated = False

    def _partner_checked(partner_id: int) -> bool:
        if checked_partner_ids is None:
            return True
        return partner_id in checked_partner_ids

    def _chat_checked(chat_id: str) -> bool:
        if checked_chat_ids is None:
            return True
        return chat_id in checked_chat_ids

    for partner in partners:
        if len(recipients) >= limit:
            truncated = True
            break
        chat_id = str(partner.chat_id or '').strip()
        if chat_id:
            seen_chat_ids.add(chat_id)
        recipients.append(
            {
                'kind': 'partner',
                'input_name': 'preview_partners',
                'input_value': str(partner.pk),
                'checked': _partner_checked(partner.pk),
                'name': partner.title or partner.chat_id or str(partner.pk),
                'chat_id': chat_id,
                'meta': partner.get_chat_type_display()
                + (f', {partner.group.name}' if partner.group else ''),
            }
        )

    for item in included:
        if len(recipients) >= limit:
            truncated = True
            break
        target = str(item.get('target') or '').strip()
        if not target or target in seen_chat_ids:
            continue
        seen_chat_ids.add(target)
        recipients.append(
            {
                'kind': 'snapshot',
                'input_name': 'preview_chat_ids',
                'input_value': target,
                'checked': _chat_checked(target),
                'name': str(item.get('name') or target),
                'chat_id': target,
                'meta': f"[{item.get('kind', '')} #{item.get('entity_id', '')}] {item.get('source', '')}",
            }
        )

    for chat_id in sorted(recipient_chat_ids):
        if len(recipients) >= limit:
            truncated = True
            break
        if chat_id in seen_chat_ids:
            continue
        seen_chat_ids.add(chat_id)
        recipients.append(
            {
                'kind': 'chat',
                'input_name': 'preview_chat_ids',
                'input_value': chat_id,
                'checked': _chat_checked(chat_id),
                'name': chat_id,
                'chat_id': chat_id,
                'meta': 'Прямой chat id',
            }
        )

    preview_limit = limit
    return {
        'recipients': recipients,
        'recipients_count': len(recipients),
        'partners_count': partners_total,
        'chat_ids_count': len(recipient_chat_ids),
        'total_count': partners_total + len(recipient_chat_ids),
        'excluded': excluded[:preview_limit],
        'excluded_truncated': len(excluded) > preview_limit,
        'recipients_truncated': truncated,
    }


@admin.register(PartnerGroup)
class PartnerGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'order', 'partner_count')
    list_editable = ('order',)
    search_fields = ('name', 'description')

    def partner_count(self, obj):
        return obj.partners.count()
    partner_count.short_description = 'Партнёров'


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = ('title', 'chat_id', 'chat_type', 'group', 'is_active', 'created_at')
    list_filter = ('chat_type', 'is_active', 'group')
    search_fields = ('title', 'chat_id')
    autocomplete_fields = ('group',)
    actions = ('export_selected_to_bitrix',)

    @admin.action(description='Экспортировать выбранных в Битрикс (контакты)')
    def export_selected_to_bitrix(self, request, queryset):
        from django.conf import settings
        from mailings.services.bitrix import add_contact

        webhook_url = getattr(settings, 'BITRIX_WEBHOOK_URL', '').strip()
        if not webhook_url:
            self.message_user(
                request,
                'Задайте BITRIX_WEBHOOK_URL в .env (исходящий вебхук Битрикс24).',
                level=messages.ERROR,
            )
            return

        ok, err = 0, 0
        for partner in queryset:
            try:
                name = partner.title or partner.chat_id or str(partner.pk)
                comments = f"Telegram: {partner.chat_id} ({partner.get_chat_type_display()})"
                if partner.group:
                    comments += f"\nГруппа: {partner.group.name}"
                add_contact(name=name, comments=comments)
                ok += 1
            except Exception as e:
                err += 1
                self.message_user(
                    request,
                    f'Ошибка для {partner}: {e}',
                    level=messages.WARNING,
                )
        if ok:
            self.message_user(
                request,
                f'В Битрикс отправлено контактов: {ok}. Ошибок: {err}.',
                level=messages.SUCCESS,
            )
        if err and not ok:
            self.message_user(request, 'Ни один контакт не создан. Проверьте BITRIX_WEBHOOK_URL.', level=messages.ERROR)


class TemplateAdminForm(forms.ModelForm):
    class Meta:
        model = Template
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        text = cleaned.get('text') or ''
        media_file = cleaned.get('media_file')
        has_media = bool(media_file)
        if text:
            validate_message_length(text, has_media=has_media)
        return cleaned


class TemplateAttachmentInline(admin.TabularInline):
    model = TemplateAttachment
    extra = 1
    fields = ('file', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    form = TemplateAdminForm
    inlines = (TemplateAttachmentInline,)
    list_display = ('name', 'text_preview', 'has_media_display', 'updated_at')
    search_fields = ('name', 'description', 'keywords')
    readonly_fields = ('created_at', 'updated_at')

    fieldsets = (
        (None, {
            'fields': ('name', 'text', 'description', 'keywords'),
        }),
        ('Медиа', {
            'fields': ('media_file',),
            'description': 'Файл совместимости (старый режим). Ниже можно добавить любое количество вложений.',
        }),
        ('Даты', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj=None, **kwargs)
        if form and 'text' in form.base_fields:
            form.base_fields['text'].widget.attrs['rows'] = 12
        return form

    def text_preview(self, obj):
        if not obj.text:
            return '—'
        return (obj.text[:80] + '…') if len(obj.text) > 80 else obj.text
    text_preview.short_description = 'Текст'

    def has_media_display(self, obj):
        return 'Да' if obj.has_media else '—'
    has_media_display.short_description = 'Медиа'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path('run-mailing/', self.admin_site.admin_view(self.run_mailing_view), name='mailings_template_run_mailing'),
            path('run-mailing/groups/', self.admin_site.admin_view(self.run_mailing_groups_api), name='mailings_run_mailing_groups'),
            path('run-mailing/partners/', self.admin_site.admin_view(self.run_mailing_partners_api), name='mailings_run_mailing_partners'),
        ]
        return custom + urls

    def run_mailing_groups_api(self, request):
        """API: список групп для выбора (поиск + пагинация по 10)."""
        if not request.user.is_staff:
            return JsonResponse({'error': 'Forbidden'}, status=403)
        q = (request.GET.get('q') or '').strip()
        page = max(1, int(request.GET.get('page', 1)))
        page_size = 10
        offset = (page - 1) * page_size
        qs = PartnerGroup.objects.annotate(partners_count=Count('partners')).order_by('order', 'name')
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
        total = qs.count()
        items = list(qs[offset:offset + page_size + 1])
        has_next = len(items) > page_size
        if has_next:
            items = items[:page_size]
        return JsonResponse({
            'items': [
                {'id': g.pk, 'name': g.name, 'partners_count': getattr(g, 'partners_count', g.partners.count())}
                for g in items
            ],
            'has_next': has_next,
            'page': page,
        })

    def run_mailing_partners_api(self, request):
        """API: список партнёров для выбора (поиск + пагинация по 10)."""
        if not request.user.is_staff:
            return JsonResponse({'error': 'Forbidden'}, status=403)
        q = (request.GET.get('q') or '').strip()
        page = max(1, int(request.GET.get('page', 1)))
        page_size = 10
        offset = (page - 1) * page_size
        qs = Partner.objects.filter(is_active=True).select_related('group').order_by('group', 'title', 'chat_id')
        if q:
            qs = qs.filter(
                Q(title__icontains=q) | Q(chat_id__icontains=q) | Q(group__name__icontains=q)
            )
        items = list(qs[offset:offset + page_size + 1])
        has_next = len(items) > page_size
        if has_next:
            items = items[:page_size]
        return JsonResponse({
            'items': [
                {
                    'id': p.pk,
                    'name': p.title or p.chat_id or str(p.pk),
                    'subtitle': '{}'.format(p.get_chat_type_display()) + (f' — {p.group.name}' if p.group else ''),
                }
                for p in items
            ],
            'has_next': has_next,
            'page': page,
        })

    def run_mailing_view(self, request):
        def _render_page(*, preview=None, selected_template_id='', selected_group_ids=None, selected_partner_ids=None,
                         selected_chat_kinds=None, city_filter='', sleep_status='',
                         selected_company_types=None, selected_potentials=None,
                         selected_contact_job_titles=None, selected_channels=None):
            templates = Template.objects.all().order_by('name')
            context = {
                **self.admin_site.each_context(request),
                'templates': templates,
                'title': 'Запуск рассылки',
                'preview': preview,
                'selected_template_id': str(selected_template_id or ''),
                'selected_group_ids': selected_group_ids or [],
                'selected_partner_ids': selected_partner_ids or [],
                'selected_chat_kinds': selected_chat_kinds or [],
                'selected_city_filter': str(city_filter or ''),
                'selected_sleep_status': str(sleep_status or ''),
                'selected_company_types': selected_company_types or [],
                'selected_potentials': selected_potentials or [],
                'selected_contact_job_titles': selected_contact_job_titles or [],
                'selected_channels': selected_channels or [],
            }
            return render(request, 'admin/mailings/run_mailing.html', context)

        if request.method == 'POST':
            from .views import (
                _collect_recipients_from_filters_data,
                _normalize_mailing_channels,
            )

            action = str(request.POST.get('_action') or 'queue').strip()
            template_id = str(request.POST.get('template_id') or '').strip()
            group_ids = [int(pk) for pk in request.POST.getlist('groups') if str(pk).strip().isdigit()]
            partner_ids = [int(pk) for pk in request.POST.getlist('partners') if str(pk).strip().isdigit()]
            chat_kinds = [v.strip() for v in request.POST.getlist('chat_kinds') if str(v).strip()]
            city_filter = (request.POST.get('city_filter') or '').strip()
            sleep_status = (request.POST.get('sleep_status') or '').strip()
            company_types = [v.strip() for v in request.POST.getlist('company_types') if str(v).strip()]
            potentials = [v.strip() for v in request.POST.getlist('potentials') if str(v).strip()]
            contact_job_titles = [v.strip() for v in request.POST.getlist('contact_job_titles') if str(v).strip()]
            channels = _normalize_mailing_channels(
                request.POST.getlist('channels'),
                default_to_max=False,
            )
            if not channels:
                messages.error(request, 'Выберите хотя бы один канал доставки: MAX или Telegram.')
                return _render_page(
                    selected_template_id=template_id,
                    selected_group_ids=group_ids,
                    selected_partner_ids=partner_ids,
                    selected_chat_kinds=chat_kinds,
                    city_filter=city_filter,
                    sleep_status=sleep_status,
                    selected_company_types=company_types,
                    selected_potentials=potentials,
                    selected_contact_job_titles=contact_job_titles,
                    selected_channels=[],
                )
            if action == 'queue' and not template_id:
                messages.error(request, 'Выберите шаблон.')
                return _render_page(
                    selected_template_id=template_id,
                    selected_group_ids=group_ids,
                    selected_partner_ids=partner_ids,
                    selected_chat_kinds=chat_kinds,
                    city_filter=city_filter,
                    sleep_status=sleep_status,
                    selected_company_types=company_types,
                    selected_potentials=potentials,
                    selected_contact_job_titles=contact_job_titles,
                    selected_channels=channels,
                )

            recipients_data = _collect_recipients_from_filters_data(
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
            partner_pks = recipients_data['partner_pks']
            recipient_chat_ids = recipients_data['recipient_chat_ids']
            allowed_partner_pks = set(partner_pks)
            allowed_chat_ids = set(recipient_chat_ids)

            partner_qs = Partner.objects.filter(pk__in=partner_pks, is_active=True).select_related('group').order_by(
                'group__name',
                'title',
                'chat_id',
            )

            def _make_preview(*, checked_partner_ids=None, checked_chat_ids=None):
                return build_mailing_preview(
                    partners=partner_qs,
                    recipient_chat_ids=allowed_chat_ids,
                    included=recipients_data['included'],
                    excluded=recipients_data['excluded'],
                    partners_total=len(partner_pks),
                    checked_partner_ids=checked_partner_ids,
                    checked_chat_ids=checked_chat_ids,
                )

            def _render_with_preview(preview):
                return _render_page(
                    preview=preview,
                    selected_template_id=template_id,
                    selected_group_ids=group_ids,
                    selected_partner_ids=partner_ids,
                    selected_chat_kinds=chat_kinds,
                    city_filter=city_filter,
                    sleep_status=sleep_status,
                    selected_company_types=company_types,
                    selected_potentials=potentials,
                    selected_contact_job_titles=contact_job_titles,
                    selected_channels=channels,
                )

            if action == 'preview':
                preview = _make_preview()
                if not partner_pks and not recipient_chat_ids:
                    messages.warning(request, 'По текущим фильтрам получателей не найдено.')
                else:
                    messages.info(request, 'Отметьте получателей и нажмите «Отправить выбранным» для теста.')
                return _render_with_preview(preview)

            if action == 'send_selected':
                if not template_id:
                    messages.error(request, 'Выберите шаблон для отправки.')
                    return _render_with_preview(
                        _make_preview(
                            checked_partner_ids={
                                int(x) for x in request.POST.getlist('preview_partners') if str(x).strip().isdigit()
                            },
                            checked_chat_ids={x.strip() for x in request.POST.getlist('preview_chat_ids') if x.strip()},
                        )
                    )
                selected_partner_ids = [
                    int(x)
                    for x in request.POST.getlist('preview_partners')
                    if str(x).strip().isdigit() and int(x) in allowed_partner_pks
                ]
                selected_chat_ids = [
                    x.strip()
                    for x in request.POST.getlist('preview_chat_ids')
                    if x.strip() and x.strip() in allowed_chat_ids
                ]
                checked_partner_ids = {
                    int(x) for x in request.POST.getlist('preview_partners') if str(x).strip().isdigit()
                }
                checked_chat_ids = {x.strip() for x in request.POST.getlist('preview_chat_ids') if x.strip()}
                preview = _make_preview(
                    checked_partner_ids=checked_partner_ids,
                    checked_chat_ids=checked_chat_ids,
                )
                if not selected_partner_ids and not selected_chat_ids:
                    messages.error(request, 'Не выбран ни один получатель.')
                    return _render_with_preview(preview)
                template = Template.objects.filter(pk=int(template_id)).first()
                if not template:
                    messages.error(request, 'Шаблон не найден.')
                    return _render_with_preview(preview)
                batch_id = Mailing.create_batch_id()
                run_mailing.delay(
                    template_id=template.pk,
                    partner_ids=selected_partner_ids,
                    recipient_chat_ids=selected_chat_ids,
                    batch_id=str(batch_id),
                    user_id=request.user.pk,
                )
                total = len(selected_partner_ids) + len(selected_chat_ids)
                messages.success(
                    request,
                    f'Тестовая рассылка поставлена в очередь: {total} получателей. '
                    f'Идентификатор запуска: {str(batch_id)[:8]}… — см. «История рассылок».',
                )
                return _render_with_preview(preview)

            if not partner_pks and not recipient_chat_ids:
                messages.error(request, 'Нет получателей. Выберите партнёров/группы или задайте фильтры snapshot.')
                return redirect('admin:mailings_template_run_mailing')
            template = Template.objects.filter(pk=int(template_id)).first()
            if not template:
                messages.error(request, 'Шаблон не найден.')
                return redirect('admin:mailings_template_run_mailing')
            partners = Partner.objects.filter(pk__in=partner_pks, is_active=True)
            batch_id = Mailing.create_batch_id()
            run_mailing.delay(
                template_id=template.pk,
                partner_ids=list(partners.values_list('pk', flat=True)),
                recipient_chat_ids=list(recipient_chat_ids),
                batch_id=str(batch_id),
                user_id=request.user.pk,
            )
            messages.success(
                request,
                'Рассылка поставлена в очередь. Итоги можно посмотреть в разделе «История рассылок» (фильтр по идентификатору запуска).'
            )
            return redirect('admin:mailings_mailing_changelist')
        return _render_page()


@admin.register(Mailing)
class MailingAdmin(admin.ModelAdmin):
    list_display = ('id', 'template', 'partner_or_recipient', 'delivery_channel', 'batch_id_short', 'sent_at', 'status', 'error_message_short')
    list_filter = ('status', 'sent_at', 'batch_id')
    search_fields = ('template__name', 'partner__title', 'partner__chat_id', 'recipient_chat_id')
    readonly_fields = ('template', 'partner', 'recipient_chat_id', 'batch_id', 'sent_at', 'status', 'error_message', 'sent_by')
    date_hierarchy = 'sent_at'

    def batch_id_short(self, obj):
        return str(obj.batch_id)[:8] + '…' if obj.batch_id else '—'
    batch_id_short.short_description = 'Запуск'

    def partner_or_recipient(self, obj):
        if obj.partner:
            return obj.partner
        return obj.recipient_chat_id or '—'
    partner_or_recipient.short_description = 'Получатель'

    def delivery_channel(self, obj):
        chat_id = obj.recipient_chat_id or (obj.partner.chat_id if obj.partner else '')
        return 'MAX' if str(chat_id or '').startswith('max:') else 'Telegram'
    delivery_channel.short_description = 'Канал'

    def error_message_short(self, obj):
        return (obj.error_message[:50] + '…') if obj.error_message and len(obj.error_message) > 50 else (obj.error_message or '—')
    error_message_short.short_description = 'Ошибка'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BitrixContactSnapshot)
class BitrixContactSnapshotAdmin(admin.ModelAdmin):
    change_list_template = 'admin/mailings/bitrix_snapshot_change_list.html'
    list_display = (
        'bitrix_contact_id',
        'full_name',
        'job_title',
        'phone',
        'email',
        'address',
        'messenger_chat_number',
        'last_sale_at',
        'bitrix_date_modified',
        'synced_at',
    )
    search_fields = (
        'bitrix_contact_id',
        'full_name',
        'job_title',
        'phone',
        'email',
        'address',
        'messenger_chat_number',
        'telegram_id',
        'max_chat_id',
    )
    readonly_fields = ('created_at', 'synced_at')

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path('sync-now/', self.admin_site.admin_view(self.sync_now_view), name='mailings_bitrixcontactsnapshot_sync_now'),
        ]
        return custom + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['sync_now_url'] = reverse('admin:mailings_bitrixcontactsnapshot_sync_now')
        extra_context['sync_now_label'] = 'Синхронизировать из Bitrix'
        return super().changelist_view(request, extra_context=extra_context)

    def sync_now_view(self, request):
        sync_bitrix_snapshots_task.delay()
        self.message_user(
            request,
            'Синхронизация поставлена в очередь Celery. Обновите страницу через минуту.',
            level=messages.SUCCESS,
        )
        return redirect('admin:mailings_bitrixcontactsnapshot_changelist')


@admin.register(BitrixCompanySnapshot)
class BitrixCompanySnapshotAdmin(admin.ModelAdmin):
    change_list_template = 'admin/mailings/bitrix_snapshot_change_list.html'
    list_display = (
        'bitrix_company_id',
        'title',
        'messenger_chat_number',
        'max_chat_id',
        'potential_value',
        'segment_value',
        'last_sale_at',
        'bitrix_date_modified',
        'synced_at',
    )
    search_fields = (
        'bitrix_company_id',
        'title',
        'messenger_chat_number',
        'max_chat_id',
        'potential_value',
        'segment_value',
        'potential_value_id',
        'segment_value_id',
    )
    readonly_fields = ('created_at', 'synced_at')

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path('sync-now/', self.admin_site.admin_view(self.sync_now_view), name='mailings_bitrixcompanysnapshot_sync_now'),
        ]
        return custom + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['sync_now_url'] = reverse('admin:mailings_bitrixcompanysnapshot_sync_now')
        extra_context['sync_now_label'] = 'Синхронизировать из Bitrix'
        return super().changelist_view(request, extra_context=extra_context)

    def sync_now_view(self, request):
        sync_bitrix_snapshots_task.delay()
        self.message_user(
            request,
            'Синхронизация поставлена в очередь Celery. Обновите страницу через минуту.',
            level=messages.SUCCESS,
        )
        return redirect('admin:mailings_bitrixcompanysnapshot_changelist')
