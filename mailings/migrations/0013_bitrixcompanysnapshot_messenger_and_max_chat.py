from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mailings', '0012_mailing_recipient_chat_id_and_nullable_partner'),
    ]

    operations = [
        migrations.AddField(
            model_name='bitrixcompanysnapshot',
            name='messenger_chat_number',
            field=models.CharField(
                blank=True,
                default='',
                max_length=255,
                verbose_name='Номер чата в мессенджере',
            ),
        ),
        migrations.AddField(
            model_name='bitrixcompanysnapshot',
            name='max_chat_id',
            field=models.CharField(
                blank=True,
                default='',
                max_length=255,
                verbose_name='MAX chat ID',
            ),
        ),
    ]
