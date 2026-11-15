import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def marquer_lots_deja_evacues(apps, schema_editor):
    """Les lots clôturés par l'ancien flux ont été évacués à la clôture même."""
    from datetime import datetime, time

    from django.utils import timezone

    LotGrossissement = apps.get_model("grossissement", "LotGrossissement")
    for lot in LotGrossissement.objects.filter(statut="CLOTURE", evacue_le__isnull=True):
        lot.evacue_le = timezone.make_aware(
            datetime.combine(lot.date_cloture, time.min),
            timezone.get_current_timezone(),
        )
        lot.save(update_fields=["evacue_le"])


class Migration(migrations.Migration):
    """Clôture sur seuil de poids, motif facultatif et évacuation différable."""

    dependencies = [
        ("grossissement", "0008_pesee_par_lot"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="lotgrossissement",
            name="lot_gross_poids_final_positif",
        ),
        migrations.RemoveConstraint(
            model_name="lotgrossissement",
            name="lot_gross_cloture_motivee",
        ),
        migrations.RenameField(
            model_name="lotgrossissement",
            old_name="poids_final_g",
            new_name="poids_moyen_final",
        ),
        migrations.AddField(
            model_name="lotgrossissement",
            name="evacue_le",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lotgrossissement",
            name="evacue_par",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lots_grossissement_evacues",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="lotgrossissement",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(poids_moyen_final__isnull=True)
                    | models.Q(poids_moyen_final__gt=0)
                ),
                name="lot_gross_poids_moyen_final_positif",
            ),
        ),
        migrations.AddConstraint(
            model_name="lotgrossissement",
            constraint=models.CheckConstraint(
                condition=~models.Q(statut="CLOTURE") | models.Q(date_cloture__isnull=False),
                name="lot_gross_cloture_datee",
            ),
        ),
        # Pas de retour arrière : l'ancien flux évacuait toujours à la clôture.
        migrations.RunPython(marquer_lots_deja_evacues, migrations.RunPython.noop),
    ]
