from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mailings', '0013_bitrixcompanysnapshot_messenger_and_max_chat'),
    ]

    operations = [
        migrations.AddField(
            model_name='bitrixcontactsnapshot',
            name='telegram_group_id',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Telegram group ID'),
        ),
        migrations.AddField(
            model_name='bitrixcompanysnapshot',
            name='telegram_group_id',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Telegram group ID'),
        ),
    ]

