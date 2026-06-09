from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('openlines', '0005_alter_maxgroupstaffmember_bitrix_user_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='maxgroupstaffmember',
            name='add_for_contact',
            field=models.BooleanField(
                default=True,
                help_text='Сотрудник попадёт в MAX-группу, если робот создаёт группу из карточки контакта.',
                verbose_name='Добавлять при создании из контакта',
            ),
        ),
        migrations.AddField(
            model_name='maxgroupstaffmember',
            name='add_for_company',
            field=models.BooleanField(
                default=True,
                help_text='Сотрудник попадёт в MAX-группу, если робот создаёт группу из карточки компании.',
                verbose_name='Добавлять при создании из компании',
            ),
        ),
    ]
