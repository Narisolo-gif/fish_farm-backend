from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("stocks", "0004_copier_mise_en_stock_hapas"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="miseenstock",
            name="hapa",
        ),
    ]
