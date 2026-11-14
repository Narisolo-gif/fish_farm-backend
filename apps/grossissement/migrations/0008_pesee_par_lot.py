import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Pesée par lot (2/2) : la pesée de bassin disparaît, PeseeLot devient Pesee.

    Les identifiants des pesées (anciennes lignes) et des indicateurs sont
    conservés. La suppression devient une annulation motivée.
    """

    dependencies = [
        ("bassins", "0006_hapa_created_at"),
        ("grossissement", "0007_pesee_par_lot_donnees"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="peseelot",
            name="pesee_lot_unique",
        ),
        migrations.RemoveField(
            model_name="peseelot",
            name="pesee",
        ),
        migrations.DeleteModel(
            name="Pesee",
        ),
        migrations.RenameModel(
            old_name="PeseeLot",
            new_name="Pesee",
        ),
        migrations.RenameField(
            model_name="indicateurlot",
            old_name="pesee_lot",
            new_name="pesee",
        ),
        migrations.AlterModelOptions(
            name="pesee",
            options={"ordering": ["-date", "-id"]},
        ),
        migrations.AlterField(
            model_name="pesee",
            name="lot_grossissement",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pesees",
                to="grossissement.lotgrossissement",
            ),
        ),
        migrations.AlterField(
            model_name="pesee",
            name="bassin",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pesees",
                to="bassins.bassin",
            ),
        ),
        migrations.AlterField(
            model_name="pesee",
            name="date",
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name="pesee",
            name="pourcentage_reel",
            field=models.DecimalField(decimal_places=2, max_digits=5),
        ),
        migrations.AlterField(
            model_name="pesee",
            name="annulee_par",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pesees_annulees",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="pesee",
            name="utilisateur",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pesees_saisies",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="pesee",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name="pesee",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name="indicateurlot",
            name="pesee",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="indicateur",
                to="grossissement.pesee",
            ),
        ),
        migrations.AddIndex(
            model_name="pesee",
            index=models.Index(
                fields=["lot_grossissement", "statut", "date"],
                name="pesee_lot_statut_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="pesee",
            index=models.Index(fields=["bassin", "date"], name="pesee_bassin_date_idx"),
        ),
        migrations.AddConstraint(
            model_name="pesee",
            constraint=models.UniqueConstraint(
                condition=models.Q(statut="VALIDEE"),
                fields=("lot_grossissement", "date"),
                name="pesee_lot_date_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="pesee",
            constraint=models.CheckConstraint(
                condition=models.Q(pourcentage_reel__gte=0, pourcentage_reel__lte=100),
                name="pesee_pourcentage_reel_valide",
            ),
        ),
        migrations.AddConstraint(
            model_name="pesee",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(statut="ANNULEE")
                    | models.Q(motif_annulation__gt="", annulee_le__isnull=False)
                ),
                name="pesee_annulation_motivee",
            ),
        ),
    ]
