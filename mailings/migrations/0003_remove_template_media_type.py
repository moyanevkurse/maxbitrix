# Один файл — без выбора типа; тип отправки по расширению файла

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mailings', '0002_simplify_partner_recipient'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='template',
            name='media_type',
        ),
    ]
