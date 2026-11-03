from django.core.exceptions import ValidationError
from django.db import models


class BitrixPortal(models.Model):
    """
    Настройки подключения к конкретному порталу Bitrix24 и OAuth-токены приложения.
    Пока предполагаем один портал на проект, но оставляем модель расширяемой.
    """

    name = models.CharField('Название портала', max_length=255, blank=True, default='')
    portal_url = models.URLField('URL портала', unique=True)

    client_id = models.CharField('ID приложения', max_length=255)
    client_secret = models.CharField('Секрет приложения', max_length=255)

    access_token = models.CharField('Access token', max_length=512, blank=True, default='')
    refresh_token = models.CharField('Refresh token', max_length=512, blank=True, default='')
    access_token_expires_at = models.DateTimeField(
        'Токен действителен до',
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Портал Bitrix24'
        verbose_name_plural = 'Порталы Bitrix24'

    def __str__(self) -> str:
        return self.name or self.portal_url


class ExternalChat(models.Model):
    """
    Связка чата Telegram с чатом/диалогом в Открытых линиях Bitrix24.
    """

    CHAT_TYPE_PRIVATE = 'private'
    CHAT_TYPE_GROUP = 'group'
    CHAT_TYPE_SUPERGROUP = 'supergroup'
    CHAT_TYPE_CHANNEL = 'channel'

    CHAT_TYPE_CHOICES = (
        (CHAT_TYPE_PRIVATE, 'Личное сообщение'),
        (CHAT_TYPE_GROUP, 'Группа'),
        (CHAT_TYPE_SUPERGROUP, 'Супергруппа'),
        (CHAT_TYPE_CHANNEL, 'Канал'),
    )

    portal = models.ForeignKey(
        BitrixPortal,
        on_delete=models.CASCADE,
        related_name='chats',
        verbose_name='Портал',
    )
    telegram_chat_id = models.CharField(
        'Telegram chat_id',
        max_length=64,
        db_index=True,
    )
    telegram_chat_type = models.CharField(
        'Тип чата Telegram',
        max_length=16,
        choices=CHAT_TYPE_CHOICES,
    )
    telegram_chat_title = models.CharField(
        'Название чата / имя',
        max_length=255,
        blank=True,
        default='',
    )

    bitrix_chat_id = models.CharField(
        'ID чата Bitrix (OPENLINE)',
        max_length=64,
        blank=True,
        default='',
        help_text='Идентификатор чата в Битрикс (imopenlines / im.chat).',
    )
    bitrix_dialog_id = models.CharField(
        'Диалог/STREAM ID',
        max_length=128,
        blank=True,
        default='',
        help_text='Строковый идентификатор диалога в Открытой линии, если используется.',
    )

    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Внешний чат (Telegram ↔ Bitrix)'
        verbose_name_plural = 'Внешние чаты (Telegram ↔ Bitrix)'
        unique_together = (
            ('portal', 'telegram_chat_id'),
        )

    def __str__(self) -> str:
        return f'{self.telegram_chat_id} → {self.portal}'


class MaxGroupWelcomeConfig(models.Model):
    """
    Конфиг приветственного текста для новых MAX-групп.

    Ожидается, что текст будет отправляться в MAX как plain text:
    - переносы строк и пробелы сохраняются как есть
    - форматирование HTML/Markdown не поддерживается без отдельного parse_mode
    """

    welcome_text = models.TextField('Текст приветствия', blank=True, default='')
    is_active = models.BooleanField('Активно', default=True)

    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Приветствие MAX-групп'
        verbose_name_plural = 'Приветствия MAX-групп'
        ordering = ['-updated_at', '-id']

    def __str__(self) -> str:
        raw = (self.welcome_text or '').strip()
        preview = raw[:60] + ('…' if len(raw) > 60 else '')
        return preview or '—'


class MaxGroupWelcomeAttachment(models.Model):
    """Вложения приветствия MAX-групп (любое количество файлов)."""

    config = models.ForeignKey(
        MaxGroupWelcomeConfig,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name='Приветствие',
    )
    file = models.FileField(
        'Файл',
        upload_to='openlines/welcome/%Y/%m/',
    )
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        verbose_name = 'Вложение приветствия'
        verbose_name_plural = 'Вложения приветствия'
        ordering = ['created_at', 'id']

    def __str__(self) -> str:
        return self.file.name or f'Attachment #{self.pk}'


class MaxGroupCreatorConfig(models.Model):
    """MAX account used to create groups for a Bitrix entity type."""

    ENTITY_CONTACT = 'contact'
    ENTITY_COMPANY = 'company'
    ENTITY_CHOICES = (
        (ENTITY_CONTACT, 'Контакт'),
        (ENTITY_COMPANY, 'Компания'),
    )

    entity_type = models.CharField(
        'Для кого создаются группы',
        max_length=16,
        choices=ENTITY_CHOICES,
        unique=True,
    )
    account_name = models.CharField(
        'Название MAX-аккаунта',
        max_length=100,
        blank=True,
        default='',
    )
    profile_dir = models.CharField(
        'Папка профиля аккаунта',
        max_length=512,
        blank=True,
        default='',
        help_text='Путь к профилю Playwright. Если пусто, используется MAX_GROUP_PROFILE_DIR из .env.',
    )
    token_file = models.CharField(
        'Файл WS-токена аккаунта',
        max_length=512,
        blank=True,
        default='',
        help_text='Путь к файлу токена этого MAX-аккаунта. Если пусто, используется MAX_GROUP_TOKEN_FILE из .env.',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Аккаунт создания MAX-групп'
        verbose_name_plural = 'Аккаунты создания MAX-групп'
        ordering = ('entity_type',)

    def clean(self):
        super().clean()
        if bool(self.profile_dir.strip()) != bool(self.token_file.strip()):
            raise ValidationError({
                'profile_dir': 'Укажите профиль и файл WS-токена вместе либо оставьте оба поля пустыми.',
                'token_file': 'Укажите профиль и файл WS-токена вместе либо оставьте оба поля пустыми.',
            })

    def __str__(self) -> str:
        entity = self.get_entity_type_display()
        return f'{entity}: {self.account_name or "MAX аккаунт"}'


class MaxGroupStaffMember(models.Model):
    """
    Сотрудники для добавления в MAX-группу при создании из контакта или компании.
    Храним телефон и MAX user_id; добавляются вместе с ответственным (ASSIGNED_BY_ID).
    """

    name = models.CharField('Имя', max_length=255, blank=True, default='')
    phone = models.CharField(
        'Телефон',
        max_length=32,
        blank=True,
        default='',
        help_text='Используется, если MAX user_id не задан — резолв через WS при создании группы.',
    )
    max_user_id = models.PositiveBigIntegerField(
        'MAX user_id',
        null=True,
        blank=True,
        help_text='Числовой ID пользователя в MAX.',
    )
    bitrix_user_id = models.CharField(
        'ID пользователя Bitrix',
        max_length=64,
        blank=True,
        default='',
        help_text='Если совпадает с ASSIGNED_BY_ID контакта/компании — этот сотрудник считается ответственным.',
    )
    add_for_contact = models.BooleanField(
        'Добавлять при создании из контакта',
        default=True,
        help_text='Сотрудник попадёт в MAX-группу, если робот создаёт группу из карточки контакта.',
    )
    add_for_company = models.BooleanField(
        'Добавлять при создании из компании',
        default=True,
        help_text='Сотрудник попадёт в MAX-группу, если робот создаёт группу из карточки компании.',
    )
    sort_order = models.PositiveIntegerField('Порядок', default=0)
    is_active = models.BooleanField('Активен', default=True)

    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Сотрудник (MAX-группы)'
        verbose_name_plural = 'Сотрудники (MAX-группы)'
        ordering = ['sort_order', 'id']

    def __str__(self) -> str:
        label = self.name or self.phone or str(self.max_user_id or self.pk)
        if self.max_user_id:
            return f'{label} (max:{self.max_user_id})'
        return label


class TelegramGroupWelcomeConfig(models.Model):
    """Редактируемое из админки приветствие для автоматически созданных TG-групп."""

    welcome_text = models.TextField('Текст приветствия', blank=True, default='')
    is_active = models.BooleanField('Активно', default=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Приветствие Telegram-групп'
        verbose_name_plural = 'Приветствия Telegram-групп'
        ordering = ['-updated_at', '-id']

    def __str__(self) -> str:
        return (self.welcome_text or '').strip()[:60] or '—'


class TelegramGroupWelcomeAttachment(models.Model):
    config = models.ForeignKey(
        TelegramGroupWelcomeConfig,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name='Приветствие',
    )
    file = models.FileField('Файл', upload_to='openlines/telegram-welcome/%Y/%m/')
    created_at = models.DateTimeField('Создан', auto_now_add=True)

    class Meta:
        verbose_name = 'Вложение приветствия Telegram-групп'
        verbose_name_plural = 'Вложения приветствия Telegram-групп'
        ordering = ['created_at', 'id']


class TelegramGroupStaffMember(models.Model):
    """Сотрудник, добавляемый служебным TG-аккаунтом в новые группы."""

    name = models.CharField('Имя', max_length=255, blank=True, default='')
    telegram_username = models.CharField(
        'Telegram username', max_length=128, blank=True, default='',
        help_text='Без @. Предпочтительный способ добавить пользователя через MTProto.',
    )
    telegram_user_id = models.PositiveBigIntegerField('Telegram user ID', null=True, blank=True)
    bitrix_user_id = models.CharField('ID пользователя Bitrix', max_length=64, blank=True, default='')
    add_for_contact = models.BooleanField('Добавлять из контакта', default=True)
    add_for_company = models.BooleanField('Добавлять из компании', default=True)
    sort_order = models.PositiveIntegerField('Порядок', default=0)
    is_active = models.BooleanField('Активен', default=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Сотрудник (Telegram-группы)'
        verbose_name_plural = 'Сотрудники (Telegram-группы)'
        ordering = ['sort_order', 'id']

    def __str__(self) -> str:
        return self.name or self.telegram_username or str(self.telegram_user_id or self.pk)


class TelegramGroupProvision(models.Model):
    """Идемпотентная заявка Bitrix на создание одной Telegram-супергруппы."""

    STATUS_QUEUED = 'queued'
    STATUS_CREATING = 'creating'
    STATUS_SUCCESS = 'success'
    STATUS_ERROR = 'error'
    STATUS_CHOICES = (
        (STATUS_QUEUED, 'В очереди'),
        (STATUS_CREATING, 'Создаётся'),
        (STATUS_SUCCESS, 'Создана'),
        (STATUS_ERROR, 'Ошибка'),
    )
    ENTITY_CONTACT = 'contact'
    ENTITY_COMPANY = 'company'
    ENTITY_CHOICES = ((ENTITY_CONTACT, 'Контакт'), (ENTITY_COMPANY, 'Компания'))

    request_key = models.CharField('Ключ идемпотентности', max_length=255, unique=True)
    entity_type = models.CharField('Тип сущности Bitrix', max_length=16, choices=ENTITY_CHOICES)
    entity_id = models.CharField('ID сущности Bitrix', max_length=64)
    title = models.CharField('Название группы', max_length=255)
    requested_user_refs = models.JSONField('Запрошенные участники', default=list, blank=True)
    telegram_chat_id = models.CharField('Telegram chat ID', max_length=64, blank=True, default='', db_index=True)
    status = models.CharField('Статус', max_length=16, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True)
    result_data = models.JSONField('Результат', default=dict, blank=True)
    error_message = models.TextField('Ошибка', blank=True, default='')
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Создание Telegram-группы'
        verbose_name_plural = 'Создания Telegram-групп'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['entity_type', 'entity_id'])]

    def __str__(self) -> str:
        return f'{self.title} [{self.get_status_display()}]'

