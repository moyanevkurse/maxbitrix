from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('openlines', '0007_maxgroupwelcomeattachment'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramGroupProvision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('request_key', models.CharField(max_length=255, unique=True, verbose_name='Ключ идемпотентности')),
                ('entity_type', models.CharField(choices=[('contact', 'Контакт'), ('company', 'Компания')], max_length=16, verbose_name='Тип сущности Bitrix')),
                ('entity_id', models.CharField(max_length=64, verbose_name='ID сущности Bitrix')),
                ('title', models.CharField(max_length=255, verbose_name='Название группы')),
                ('requested_user_refs', models.JSONField(blank=True, default=list, verbose_name='Запрошенные участники')),
                ('telegram_chat_id', models.CharField(blank=True, db_index=True, default='', max_length=64, verbose_name='Telegram chat ID')),
                ('status', models.CharField(choices=[('queued', 'В очереди'), ('creating', 'Создаётся'), ('success', 'Создана'), ('error', 'Ошибка')], db_index=True, default='queued', max_length=16, verbose_name='Статус')),
                ('result_data', models.JSONField(blank=True, default=dict, verbose_name='Результат')),
                ('error_message', models.TextField(blank=True, default='', verbose_name='Ошибка')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
            ],
            options={
                'verbose_name': 'Создание Telegram-группы',
                'verbose_name_plural': 'Создания Telegram-групп',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='TelegramGroupStaffMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(blank=True, default='', max_length=255, verbose_name='Имя')),
                ('telegram_username', models.CharField(blank=True, default='', help_text='Без @. Предпочтительный способ добавить пользователя через MTProto.', max_length=128, verbose_name='Telegram username')),
                ('telegram_user_id', models.PositiveBigIntegerField(blank=True, null=True, verbose_name='Telegram user ID')),
                ('bitrix_user_id', models.CharField(blank=True, default='', max_length=64, verbose_name='ID пользователя Bitrix')),
                ('add_for_contact', models.BooleanField(default=True, verbose_name='Добавлять из контакта')),
                ('add_for_company', models.BooleanField(default=True, verbose_name='Добавлять из компании')),
                ('sort_order', models.PositiveIntegerField(default=0, verbose_name='Порядок')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активен')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
            ],
            options={
                'verbose_name': 'Сотрудник (Telegram-группы)',
                'verbose_name_plural': 'Сотрудники (Telegram-группы)',
                'ordering': ['sort_order', 'id'],
            },
        ),
        migrations.CreateModel(
            name='TelegramGroupWelcomeConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('welcome_text', models.TextField(blank=True, default='', verbose_name='Текст приветствия')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активно')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
            ],
            options={
                'verbose_name': 'Приветствие Telegram-групп',
                'verbose_name_plural': 'Приветствия Telegram-групп',
                'ordering': ['-updated_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='TelegramGroupWelcomeAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(upload_to='openlines/telegram-welcome/%Y/%m/', verbose_name='Файл')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создан')),
                ('config', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='openlines.telegramgroupwelcomeconfig', verbose_name='Приветствие')),
            ],
            options={
                'verbose_name': 'Вложение приветствия Telegram-групп',
                'verbose_name_plural': 'Вложения приветствия Telegram-групп',
                'ordering': ['created_at', 'id'],
            },
        ),
        migrations.AddIndex(
            model_name='telegramgroupprovision',
            index=models.Index(fields=['entity_type', 'entity_id'], name='openlines_t_entity__0f18b6_idx'),
        ),
    ]

