from django.urls import path

from .views import (
    bitrix_handler,
    max_handler,
    bitrix_install,
    max_create_group_webhook,
    max_create_group_debug_webhook,
    telegram_create_group_webhook,
)

urlpatterns = [
    path('handler/', bitrix_handler, name='openlines_bitrix_handler'),
    path('install/', bitrix_install, name='openlines_bitrix_install'),
    path('max/webhook/', max_handler, name='openlines_max_handler'),
    path('max/create-group/', max_create_group_webhook, name='openlines_max_create_group_webhook'),
    path('max/create-group/debug/', max_create_group_debug_webhook, name='openlines_max_create_group_debug_webhook'),
    path('telegram/create-group/', telegram_create_group_webhook, name='openlines_telegram_create_group_webhook'),
]

