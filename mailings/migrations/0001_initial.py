# Generated manually for mailings app

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Partner',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Название')),
                ('description', models.TextField(blank=True, verbose_name='Описание')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активен')),
            ],
            options={
                'verbose_name': 'Партнёр',
                'verbose_name_plural': 'Партнёры',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Template',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Название')),
                ('text', models.TextField(verbose_name='Текст сообщения')),
                ('description', models.TextField(blank=True, verbose_name='Описание')),
                ('keywords', models.CharField(blank=True, max_length=500, verbose_name='Ключевые слова')),
                ('media_type', models.CharField(blank=True, choices=[('', 'Без медиа'), ('photo', 'Фото'), ('video', 'Видео'), ('document', 'Документ')], default='', max_length=20, verbose_name='Тип медиа')),
                ('media_file', models.FileField(blank=True, null=True, upload_to='mailings/media/%Y/%m/', verbose_name='Медиафайл')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создан')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменён')),
            ],
            options={
                'verbose_name': 'Шаблон рассылки',
                'verbose_name_plural': 'Шаблоны рассылки',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.CreateModel(
            name='Recipient',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('chat_id', models.CharField(max_length=255, unique=True, verbose_name='ID чата / username')),
                ('chat_type', models.CharField(choices=[('group', 'Группа'), ('channel', 'Канал'), ('private', 'Личное сообщение')], max_length=20, verbose_name='Тип')),
                ('title', models.CharField(blank=True, max_length=255, verbose_name='Название')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активен')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создан')),
                ('partner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='recipients', to='mailings.partner', verbose_name='Партнёр')),
            ],
            options={
                'verbose_name': 'Получатель',
                'verbose_name_plural': 'Получатели',
                'ordering': ['title', 'chat_id'],
            },
        ),
        migrations.CreateModel(
            name='Mailing',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('batch_id', models.UUIDField(db_index=True, editable=False, verbose_name='Идентификатор запуска')),
                ('sent_at', models.DateTimeField(auto_now_add=True, verbose_name='Отправлено')),
                ('status', models.CharField(choices=[('success', 'Успех'), ('error', 'Ошибка')], default='success', max_length=20, verbose_name='Статус')),
                ('error_message', models.TextField(blank=True, verbose_name='Текст ошибки')),
                ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mailing_records', to='mailings.recipient', verbose_name='Получатель')),
                ('sent_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='mailings', to=settings.AUTH_USER_MODEL, verbose_name='Кто отправил')),
                ('template', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mailing_records', to='mailings.template', verbose_name='Шаблон')),
            ],
            options={
                'verbose_name': 'Запись рассылки',
                'verbose_name_plural': 'История рассылок',
                'ordering': ['-sent_at'],
            },
        ),
        migrations.AddField(
            model_name='template',
            name='partners',
            field=models.ManyToManyField(blank=True, related_name='templates', to='mailings.partner', verbose_name='Партнёры'),
        ),
    ]
