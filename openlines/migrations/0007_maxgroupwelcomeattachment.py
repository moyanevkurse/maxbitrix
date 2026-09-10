# Generated manually for MaxGroupWelcomeAttachment

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('openlines', '0006_maxgroupstaffmember_roster_scope'),
    ]

    operations = [
        migrations.CreateModel(
            name='MaxGroupWelcomeAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(upload_to='openlines/welcome/%Y/%m/', verbose_name='Файл')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создан')),
                (
                    'config',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='attachments',
                        to='openlines.maxgroupwelcomeconfig',
                        verbose_name='Приветствие',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Вложение приветствия',
                'verbose_name_plural': 'Вложения приветствия',
                'ordering': ['created_at', 'id'],
            },
        ),
    ]
