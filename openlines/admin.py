from django import forms
from django.contrib import admin

from .models import BitrixPortal, ExternalChat, MaxGroupStaffMember, MaxGroupWelcomeConfig


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


@admin.register(MaxGroupWelcomeConfig)
class MaxGroupWelcomeConfigAdmin(admin.ModelAdmin):
    list_display = ('is_active', 'updated_at', 'created_at', 'welcome_preview')
    list_filter = ('is_active',)
    readonly_fields = ('created_at', 'updated_at')
    search_fields = ('welcome_text',)

    formfield_overrides = {
        'welcome_text': {
            'widget': forms.Textarea(attrs={'rows': 18, 'style': 'width: 100%;'}),
        },
    }

    def welcome_preview(self, obj: MaxGroupWelcomeConfig) -> str:
        raw = (obj.welcome_text or '').strip()
        return raw[:80] + ('…' if len(raw) > 80 else '')

    welcome_preview.short_description = 'Превью'

    def has_add_permission(self, request) -> bool:
        # Singleton: один конфиг в админке.
        return not MaxGroupWelcomeConfig.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        # Удаление запрещаем: оставляем только редактирование.
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

