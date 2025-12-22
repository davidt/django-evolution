from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('migrations_app', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='MigrationsAppTestModel',
            name='added_field',
            field=models.IntegerField(default=42)),
    ]
