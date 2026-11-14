from decimal import ROUND_HALF_UP, Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def recopier_pesee_mere(apps, schema_editor):
    """Chaque ligne de pesée reçoit les données de sa pesée de bassin.

    Le pourcentage réel est calculé comme le fait le service :
    nombre pesé × 100 / effectif vivant, arrondi au demi supérieur.
    """
    PeseeLot = apps.get_model("grossissement", "PeseeLot")
    lignes = list(PeseeLot.objects.select_related("pesee"))
    for ligne in lignes:
        pesee = ligne.pesee
        ligne.bassin_id = pesee.bassin_id
        ligne.date = pesee.date
        ligne.utilisateur_id = pesee.utilisateur_id
        ligne.observation = pesee.observation
        ligne.created_at = pesee.created_at
        ligne.updated_at = pesee.updated_at
        ligne.statut = "VALIDEE"
        ligne.pourcentage_reel = (
            Decimal(ligne.nombre_poissons_peses)
            * Decimal(100)
            / Decimal(ligne.effectif_vivant)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    PeseeLot.objects.bulk_update(
        lignes,
        [
            "bassin",
            "date",
            "utilisateur",
            "observation",
            "created_at",
            "updated_at",
            "statut",
            "pourcentage_reel",
        ],
        batch_size=500,
    )


class Migration(migrations.Migration):
    """Pesée par lot (1/2) : les lignes reçoivent les données de leur pesée.

    Les nouveaux champs sont d'abord facultatifs ; la migration suivante
    supprime la pesée de bassin et renomme PeseeLot en Pesee.
    """

    dependencies = [
        ("bassins", "0006_hapa_created_at"),
        ("grossissement", "0006_renommer_poids_moyen_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="peseelot",
            name="bassin",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="bassins.bassin",
            ),
        ),
        migrations.AddField(
            model_name="peseelot",
            name="date",
            field=models.DateField(null=True),
        ),
        migrations.AddField(
            model_name="peseelot",
            name="pourcentage_reel",
            field=models.DecimalField(decimal_places=2, max_digits=5, null=True),
        ),
        migrations.AddField(
            model_name="peseelot",
            name="observation",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="peseelot",
            name="statut",
            field=models.CharField(
                choices=[("VALIDEE", "Validée"), ("ANNULEE", "Annulée")],
                default="VALIDEE",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="peseelot",
            name="motif_annulation",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="peseelot",
            name="annulee_par",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="peseelot",
            name="annulee_le",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="peseelot",
            name="utilisateur",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="peseelot",
            name="created_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name="peseelot",
            name="updated_at",
            field=models.DateTimeField(null=True),
        ),
        # Pas de retour arrière des données : la structure par bassin disparaît.
        migrations.RunPython(recopier_pesee_mere, migrations.RunPython.noop),
    ]
