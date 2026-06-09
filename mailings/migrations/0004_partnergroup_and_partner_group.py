# Группы партнёров: у партнёра — выбор группы, при рассылке можно выбирать группы и/или партнёров

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mailings', '0003_remove_template_media_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='PartnerGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Название')),
                ('description', models.TextField(blank=True, verbose_name='Описание')),
                ('order', models.PositiveIntegerField(default=0, verbose_name='Порядок')),
            ],
            options={
                'verbose_name': 'Группа партнёров',
                'verbose_name_plural': 'Группы партнёров',
                'ordering': ['order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='partner',
            name='group',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='partners',
                to='mailings.partnergroup',
                verbose_name='Группа',
            ),
        ),
    ]
