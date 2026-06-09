# Упрощение: партнёры = получатели (чат в Telegram), убираем отдельную категорию

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mailings', '0001_initial'),
    ]

    operations = [
        # Убрать у шаблона связь M2M с «категориями» партнёров
        migrations.RemoveField(
            model_name='template',
            name='partners',
        ),
        # У получателя убрать FK на старую категорию «партнёр»
        migrations.RemoveField(
            model_name='recipient',
            name='partner',
        ),
        # Удалить старую модель «категория партнёр» (сантехника, отделка и т.д.)
        migrations.DeleteModel(
            name='Partner',
        ),
        # Переименовать модель: Получатель → Партнёр (таблица mailings_recipient → mailings_partner)
        migrations.RenameModel(
            old_name='Recipient',
            new_name='Partner',
        ),
        # В истории рассылки: поле recipient → partner
        migrations.RenameField(
            model_name='mailing',
            old_name='recipient',
            new_name='partner',
        ),
    ]
