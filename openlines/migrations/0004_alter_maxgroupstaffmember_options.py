from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('openlines', '0003_maxgroupstaffmember'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='maxgroupstaffmember',
            options={
                'ordering': ['sort_order', 'id'],
                'verbose_name': 'Сотрудник (MAX-группы)',
                'verbose_name_plural': 'Сотрудники (MAX-группы)',
            },
        ),
    ]
