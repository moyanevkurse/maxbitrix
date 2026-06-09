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

