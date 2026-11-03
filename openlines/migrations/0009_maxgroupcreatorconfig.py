from django.db import migrations, models


def create_entity_configs(apps, schema_editor):
    MaxGroupCreatorConfig = apps.get_model('openlines', 'MaxGroupCreatorConfig')
    database = schema_editor.connection.alias
    MaxGroupCreatorConfig.objects.using(database).bulk_create([
        MaxGroupCreatorConfig(
            entity_type='contact',
            account_name='MAX аккаунт для контактов',
        ),
        MaxGroupCreatorConfig(
            entity_type='company',
            account_name='MAX аккаунт для компаний',
        ),
    ])


class Migration(migrations.Migration):

    dependencies = [
        ('openlines', '0008_telegram_group_automation'),
    ]

    operations = [
        migrations.CreateModel(
            name='MaxGroupCreatorConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('entity_type', models.CharField(
                    choices=[('contact', 'Контакт'), ('company', 'Компания')],
                    max_length=16,
                    unique=True,
                    verbose_name='Для кого создаются группы',
                )),
                ('account_name', models.CharField(
                    blank=True,
                    default='',
                    max_length=100,
                    verbose_name='Название MAX-аккаунта',
                )),
                ('profile_dir', models.CharField(
                    blank=True,
                    default='',
                    help_text='Путь к профилю Playwright. Если пусто, используется MAX_GROUP_PROFILE_DIR из .env.',
                    max_length=512,
                    verbose_name='Папка профиля аккаунта',
                )),
                ('token_file', models.CharField(
                    blank=True,
                    default='',
                    help_text='Путь к файлу токена этого MAX-аккаунта. Если пусто, используется MAX_GROUP_TOKEN_FILE из .env.',
                    max_length=512,
                    verbose_name='Файл WS-токена аккаунта',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
            ],
            options={
                'verbose_name': 'Аккаунт создания MAX-групп',
                'verbose_name_plural': 'Аккаунты создания MAX-групп',
                'ordering': ('entity_type',),
            },
        ),
        migrations.RunPython(create_entity_configs, migrations.RunPython.noop),
    ]
