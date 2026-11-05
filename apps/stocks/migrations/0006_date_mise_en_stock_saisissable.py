import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("stocks", "0005_supprimer_miseenstock_hapa"),
    ]

    operations = [
        migrations.AlterField(
            model_name="miseenstock",
            name="date_mise_en_stock",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
    ]
