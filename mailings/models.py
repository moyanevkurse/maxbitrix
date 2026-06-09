import uuid
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError


class PartnerGroup(models.Model):
    """Группа партнёров (например: сантехника, отделка, двери). Для удобного выбора при рассылке."""
    name = models.CharField('Название', max_length=255)
    description = models.TextField('Описание', blank=True)
    order = models.PositiveIntegerField(
        'Порядок',
        default=0,
        help_text='Порядок сортировки: меньшее число — выше в списке (0, 1, 2…).',
    )

    class Meta:
        verbose_name = 'Группа партнёров'
        verbose_name_plural = 'Группы партнёров'
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class Partner(models.Model):
    """
    Партнёр = кому шлём рассылку (чат в Telegram).
    Группа, канал или личка — добавляешь бота или пишешь ему, запись создаётся сама.
    Можно отнести партнёра к группе (селект), чтобы при рассылке выбирать целой группой.
    """
    TYPE_GROUP = 'group'
    TYPE_CHANNEL = 'channel'
    TYPE_PRIVATE = 'private'
    TYPE_CHOICES = [
        (TYPE_GROUP, 'Группа'),
        (TYPE_CHANNEL, 'Канал'),
        (TYPE_PRIVATE, 'Личное сообщение'),
    ]

    chat_id = models.CharField('ID чата / username', max_length=255, unique=True)
    chat_type = models.CharField('Тип', max_length=20, choices=TYPE_CHOICES)
    title = models.CharField('Название', max_length=255, blank=True)
    group = models.ForeignKey(
        'PartnerGroup', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='partners', verbose_name='Группа'
    )
    is_active = models.BooleanField('Активен', default=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        verbose_name = 'Партнёр'
        verbose_name_plural = 'Партнёры'
        ordering = ['group', 'title', 'chat_id']

    def __str__(self):
        return self.title or self.chat_id or str(self.pk)


class Template(models.Model):
    """Шаблон рассылки: текст и опционально один файл (фото, видео, документ — что угодно)."""

    name = models.CharField('Название', max_length=255)
    text = models.TextField('Текст сообщения')
    description = models.TextField('Описание', blank=True)
    keywords = models.CharField('Ключевые слова', max_length=500, blank=True)
    media_file = models.FileField(
        'Медиафайл', upload_to='mailings/media/%Y/%m/', blank=True, null=True,
        help_text='Опционально: любой файл (фото, видео, PDF и т.д.). Текст уйдёт подписью. Тип отправки определится по файлу.',
    )
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Изменён', auto_now=True)

    class Meta:
        verbose_name = 'Шаблон рассылки'
        verbose_name_plural = 'Шаблоны рассылки'
        ordering = ['-updated_at']

    def __str__(self):
        return self.name

    @property
    def has_media(self):
        if bool(self.media_file):
            return True
        if not self.pk:
            return False
        return self.attachments.exists()

    def clean(self):
        from mailings.validators import validate_message_length
        if self.text:
            validate_message_length(self.text, has_media=self.has_media)


class Mailing(models.Model):
    """История одной отправки: один шаблон — один партнёр."""
    STATUS_SUCCESS = 'success'
    STATUS_ERROR = 'error'
    STATUS_CHOICES = [
        (STATUS_SUCCESS, 'Успех'),
        (STATUS_ERROR, 'Ошибка'),
    ]

    template = models.ForeignKey(
        Template, on_delete=models.CASCADE, related_name='mailing_records',
        verbose_name='Шаблон'
    )
    partner = models.ForeignKey(
        Partner,
        on_delete=models.SET_NULL,
        related_name='mailing_records',
        verbose_name='Партнёр',
        null=True,
        blank=True,
    )
    recipient_chat_id = models.CharField('ID получателя', max_length=255, blank=True, default='')
    batch_id = models.UUIDField('Идентификатор запуска', db_index=True, editable=False)
    sent_at = models.DateTimeField('Отправлено', auto_now_add=True)
    status = models.CharField(
        'Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_SUCCESS
    )
    error_message = models.TextField('Текст ошибки', blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='mailings', verbose_name='Кто отправил'
    )

    class Meta:
        verbose_name = 'Запись рассылки'
        verbose_name_plural = 'История рассылок'
        ordering = ['-sent_at']

    def __str__(self):
        return f'{self.template.name} → {self.partner} ({self.get_status_display()})'

    @classmethod
    def create_batch_id(cls):
        return uuid.uuid4()


class TemplateAttachment(models.Model):
    """Дополнительные вложения шаблона (поддерживается любое количество файлов)."""

    template = models.ForeignKey(
        Template,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name='Шаблон',
    )
    file = models.FileField(
        'Файл',
        upload_to='mailings/media/%Y/%m/',
    )
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        verbose_name = 'Вложение шаблона'
        verbose_name_plural = 'Вложения шаблона'
        ordering = ['created_at', 'id']

    def __str__(self):
        return self.file.name or f'Attachment #{self.pk}'


class BitrixContactSnapshot(models.Model):
    """Локальный слепок контакта из Bitrix24 для последующего парсинга и синхронизации."""

    bitrix_contact_id = models.BigIntegerField('ID контакта в Bitrix24', unique=True)
    full_name = models.CharField('ФИО', max_length=255, blank=True, default='')
    assigned_by_id = models.CharField('ID ответственного', max_length=64, blank=True, default='')
    phone = models.CharField('Телефон', max_length=255, blank=True, default='')
    email = models.CharField('Email', max_length=255, blank=True, default='')
    job_title = models.CharField('Должность', max_length=255, blank=True, default='')
    address = models.CharField('Адрес', max_length=512, blank=True, default='')
    messenger_chat_number = models.CharField('Номер чата в мессенджере', max_length=255, blank=True, default='')
    last_sale_at = models.DateTimeField('Дата последней продажи (контакт)', null=True, blank=True)
    telegram_id = models.CharField('Telegram ID', max_length=255, blank=True, default='')
    max_chat_id = models.CharField('MAX chat ID', max_length=255, blank=True, default='')
    bitrix_date_modified = models.DateTimeField('Изменён в Bitrix', null=True, blank=True)
    raw_data = models.JSONField('Сырой ответ Bitrix', default=dict, blank=True)
    parsed_data = models.JSONField('Нормализованные данные', default=dict, blank=True)
    synced_at = models.DateTimeField('Синхронизировано', auto_now=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        verbose_name = 'Слепок контакта Bitrix'
        verbose_name_plural = 'Слепки контактов Bitrix'
        ordering = ['-synced_at', '-bitrix_contact_id']

    def __str__(self):
        return f'Contact #{self.bitrix_contact_id} {self.full_name}'.strip()


class BitrixCompanySnapshot(models.Model):
    """Локальный слепок компании из Bitrix24 для синка с внешними системами."""

    bitrix_company_id = models.BigIntegerField('ID компании в Bitrix24', unique=True)
    title = models.CharField('Название', max_length=512, blank=True, default='')
    assigned_by_id = models.CharField('ID ответственного', max_length=64, blank=True, default='')
    address = models.TextField('Адрес', blank=True, default='')
    potential_value_id = models.CharField('Потенциал МойСклад (ID)', max_length=64, blank=True, default='')
    potential_value = models.CharField('Потенциал МойСклад', max_length=255, blank=True, default='')
    segment_value_id = models.CharField('Тип компании (сегмент) ID', max_length=64, blank=True, default='')
    segment_value = models.CharField('Тип компании (сегмент)', max_length=255, blank=True, default='')
    last_sale_at = models.DateTimeField('Дата последней продажи', null=True, blank=True)
    messenger_chat_number = models.CharField('Номер чата в мессенджере', max_length=255, blank=True, default='')
    max_chat_id = models.CharField('MAX chat ID', max_length=255, blank=True, default='')
    bitrix_date_modified = models.DateTimeField('Изменена в Bitrix', null=True, blank=True)
    raw_data = models.JSONField('Сырой ответ Bitrix', default=dict, blank=True)
    parsed_data = models.JSONField('Нормализованные данные', default=dict, blank=True)
    synced_at = models.DateTimeField('Синхронизировано', auto_now=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        verbose_name = 'Слепок компании Bitrix'
        verbose_name_plural = 'Слепки компаний Bitrix'
        ordering = ['-synced_at', '-bitrix_company_id']

    def __str__(self):
        return f'Company #{self.bitrix_company_id} {self.title}'.strip()
