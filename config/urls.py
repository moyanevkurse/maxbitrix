from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

from mailings.views import bitrix_contact_mailing_webhook, bitrix_run_mailing_webhook, bitrix_webhook

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/bitrix/webhook/', bitrix_webhook),
    path('api/bitrix/contact-mailing/', bitrix_contact_mailing_webhook),
    path('api/bitrix/run-mailing/', bitrix_run_mailing_webhook),
    path('bitrix/', include('openlines.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
