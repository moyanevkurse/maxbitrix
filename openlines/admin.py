from django import forms
from django.contrib import admin

from .models import (
    BitrixPortal,
    ExternalChat,
    MaxGroupCreatorConfig,
    MaxGroupStaffMember,
    MaxGroupWelcomeAttachment,
    MaxGroupWelcomeConfig,
    TelegramGroupProvision,
    TelegramGroupStaffMember,
    TelegramGroupWelcomeAttachment,
    TelegramGroupWelcomeConfig,
)


@admin.register(BitrixPortal)
class BitrixPortalAdmin(admin.ModelAdmin):
    list_display = ('portal_url', 'name', 'access_token_short', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')
    search_fields = ('portal_url', 'name')

    def access_token_short(self, obj):
        if not obj.access_token:
            return '—'
        return obj.access_token[:10] + '…'

    access_token_short.short_description = 'Access token'


@admin.register(ExternalChat)
class ExternalChatAdmin(admin.ModelAdmin):
    list_display = (
        'telegram_chat_id',
        'telegram_chat_type',
        'telegram_chat_title',
        'portal',
        'bitrix_chat_id',
        'bitrix_dialog_id',
        'created_at',
    )
    list_filter = ('telegram_chat_type', 'portal')
    search_fields = ('telegram_chat_id', 'telegram_chat_title', 'bitrix_chat_id', 'bitrix_dialog_id')
    readonly_fields = ('created_at', 'updated_at')


class MaxGroupWelcomeAttachmentInline(admin.TabularInline):
    model = MaxGroupWelcomeAttachment
    extra = 1
    fields = ('file', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(MaxGroupWelcomeConfig)
class MaxGroupWelcomeConfigAdmin(admin.ModelAdmin):
    list_display = ('is_active', 'attachments_count', 'updated_at', 'created_at', 'welcome_preview')
    list_filter = ('is_active',)
    readonly_fields = ('created_at', 'updated_at')
    search_fields = ('welcome_text',)
    inlines = (MaxGroupWelcomeAttachmentInline,)

    formfield_overrides = {
        'welcome_text': {
            'widget': forms.Textarea(attrs={'rows': 18, 'style': 'width: 100%;'}),
        },
    }

    def welcome_preview(self, obj: MaxGroupWelcomeConfig) -> str:
        raw = (obj.welcome_text or '').strip()
        return raw[:80] + ('…' if len(raw) > 80 else '')

    welcome_preview.short_description = 'Превью'

    def attachments_count(self, obj: MaxGroupWelcomeConfig) -> int:
        return obj.attachments.count()

    attachments_count.short_description = 'Файлов'

    def has_add_permission(self, request) -> bool:
        # Singleton: один конфиг в админке.
        return not MaxGroupWelcomeConfig.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        # Удаление запрещаем: оставляем только редактирование.
        return False


@admin.register(MaxGroupCreatorConfig)
class MaxGroupCreatorConfigAdmin(admin.ModelAdmin):
    list_display = ('entity_type', 'account_name', 'profile_dir', 'token_file', 'updated_at')
    list_editable = ('account_name',)
    list_filter = ('entity_type',)
    search_fields = ('account_name', 'profile_dir', 'token_file')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Маршрутизация', {
            'fields': ('entity_type', 'account_name'),
        }),
        ('Данные MAX-аккаунта', {
            'fields': ('profile_dir', 'token_file'),
            'description': (
                'Укажите отдельный профиль и файл WS-токена для аккаунта, который создаёт группы. '
                'Текущий MAX-бот остаётся общим для контактов и компаний.'
            ),
        }),
        ('Служебное', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def has_add_permission(self, request):
        # Две записи (контакты и компании) создаются миграцией.
        return MaxGroupCreatorConfig.objects.count() < len(MaxGroupCreatorConfig.ENTITY_CHOICES)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MaxGroupStaffMember)
class MaxGroupStaffMemberAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'phone',
        'max_user_id',
        'bitrix_user_id',
        'add_for_contact',
        'add_for_company',
        'sort_order',
        'is_active',
        'updated_at',
    )
    list_editable = ('add_for_contact', 'add_for_company', 'sort_order', 'is_active')
    list_filter = ('is_active', 'add_for_contact', 'add_for_company')
    search_fields = ('name', 'phone', 'bitrix_user_id')
    ordering = ('sort_order', 'id')
    fieldsets = (
        (None, {
            'fields': (
                'name',
                'phone',
                'max_user_id',
                'bitrix_user_id',
                'add_for_contact',
                'add_for_company',
                'sort_order',
                'is_active',
            ),
        }),
        ('Служебное', {
            'fields': ('created_at', 'updated_at'),
        }),
    )
    readonly_fields = ('created_at', 'updated_at')

class TelegramGroupWelcomeAttachmentInline(admin.TabularInline):
    model = TelegramGroupWelcomeAttachment
    extra = 1
    fields = ('file', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(TelegramGroupWelcomeConfig)
class TelegramGroupWelcomeConfigAdmin(admin.ModelAdmin):
    list_display = ('is_active', 'updated_at', 'created_at', 'welcome_preview')
    list_filter = ('is_active',)
    readonly_fields = ('created_at', 'updated_at')
    inlines = (TelegramGroupWelcomeAttachmentInline,)

    def welcome_preview(self, obj):
        text = (obj.welcome_text or '').strip()
        return text[:80] + ('…' if len(text) > 80 else '')

    welcome_preview.short_description = 'Превью'

    def has_add_permission(self, request):
        return not TelegramGroupWelcomeConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TelegramGroupStaffMember)
class TelegramGroupStaffMemberAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'telegram_username', 'telegram_user_id', 'bitrix_user_id',
        'add_for_contact', 'add_for_company', 'sort_order', 'is_active',
    )
    list_editable = ('add_for_contact', 'add_for_company', 'sort_order', 'is_active')
    list_filter = ('is_active', 'add_for_contact', 'add_for_company')
    search_fields = ('name', 'telegram_username', 'bitrix_user_id')
    ordering = ('sort_order', 'id')


@admin.register(TelegramGroupProvision)
class TelegramGroupProvisionAdmin(admin.ModelAdmin):
    list_display = ('title', 'entity_type', 'entity_id', 'telegram_chat_id', 'status', 'created_at', 'updated_at')
    list_filter = ('status', 'entity_type')
    search_fields = ('title', 'entity_id', 'telegram_chat_id', 'request_key')
    readonly_fields = ('request_key', 'telegram_chat_id', 'status', 'result_data', 'error_message', 'created_at', 'updated_at')
