from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('move_to_migrations_app', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='MoveToMigrationsAppTestModel',
            name='added_field2',
            field=models.IntegerField(default=42)),
    ]
