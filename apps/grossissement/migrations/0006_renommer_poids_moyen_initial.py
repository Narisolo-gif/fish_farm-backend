from django.db import migrations, models


class Migration(migrations.Migration):
    """Renomme poids_initial_g en poids_moyen_initial, sans perte de données."""

    dependencies = [
        ("grossissement", "0005_lot_trace_pesee_initiale"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="lotgrossissement",
            name="lot_gross_poids_initial_positif",
        ),
        migrations.RenameField(
            model_name="lotgrossissement",
            old_name="poids_initial_g",
            new_name="poids_moyen_initial",
        ),
        migrations.AddConstraint(
            model_name="lotgrossissement",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(poids_moyen_initial__isnull=True)
                    | models.Q(poids_moyen_initial__gt=0)
                ),
                name="lot_gross_poids_moyen_initial_positif",
            ),
        ),
    ]
