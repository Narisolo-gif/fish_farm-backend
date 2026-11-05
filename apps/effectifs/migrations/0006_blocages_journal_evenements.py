import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def migrer_statuts(apps, schema_editor):
    Evenement = apps.get_model("effectifs", "EvenementMortalitePerte")
    Evenement.objects.filter(statut="ACTIF").update(statut="VALIDE")
    Evenement.objects.filter(statut="ANNULE").update(
        statut="ANNULEE",
        motif_annulation="Annulation antérieure à la mise à jour du journal",
    )


def initialiser_referentiels(apps, schema_editor):
    CauseMortalite = apps.get_model("effectifs", "CauseMortalite")
    CategoriePerte = apps.get_model("effectifs", "CategoriePerte")
    for libelle in (
        "Inconnue",
        "Maladie",
        "Manque d'oxygène",
        "Qualité de l'eau",
        "Stress de manipulation",
        "Variation de température",
        "Autre",
    ):
        CauseMortalite.objects.get_or_create(libelle=libelle)
    for libelle in ("Fuite", "Vol", "Prédation", "Écart d'inventaire", "Autre"):
        CategoriePerte.objects.get_or_create(libelle=libelle)


class Migration(migrations.Migration):

    dependencies = [
        ("effectifs", "0005_evenementmortaliteperte_hapa_sortiestock_hapa_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BlocageSaisie",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("operation_type", models.CharField(max_length=100)),
                ("operation_id", models.PositiveIntegerField()),
                ("motif", models.TextField()),
                ("actif", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("leve_le", models.DateTimeField(blank=True, null=True)),
                (
                    "hapa",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="blocages_saisie",
                        to="bassins.hapa",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="blocagesaisie",
            constraint=models.UniqueConstraint(
                condition=models.Q(("actif", True)),
                fields=("hapa", "operation_type", "operation_id"),
                name="blocage_saisie_hapa_operation_actif_unique",
            ),
        ),
        migrations.RenameField(
            model_name="evenementmortaliteperte",
            old_name="saisi_le",
            new_name="created_at",
        ),
        migrations.RenameField(
            model_name="evenementmortaliteperte",
            old_name="saisi_par",
            new_name="utilisateur",
        ),
        migrations.AlterField(
            model_name="evenementmortaliteperte",
            name="utilisateur",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="evenements_mortalite_perte_crees",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="evenementmortaliteperte",
            name="date_evenement",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name="evenementmortaliteperte",
            name="observation",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="evenementmortaliteperte",
            name="motif_annulation",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="evenementmortaliteperte",
            name="annulee_le",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="evenementmortaliteperte",
            name="annulee_par",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="evenements_mortalite_perte_annules",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(migrer_statuts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="evenementmortaliteperte",
            name="statut",
            field=models.CharField(
                choices=[("VALIDE", "Validé"), ("ANNULEE", "Annulé")],
                default="VALIDE",
                max_length=10,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="evenementmortaliteperte",
            name="evenement_mortalite_perte_referentiel_valide",
        ),
        migrations.AddConstraint(
            model_name="evenementmortaliteperte",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("categorie_perte__isnull", True),
                        ("cause_mortalite__isnull", False),
                        ("type_evenement", "MORTALITE"),
                    )
                    | models.Q(
                        ("categorie_perte__isnull", False),
                        ("cause_mortalite__isnull", True),
                        ("justification__gt", ""),
                        ("type_evenement", "PERTE"),
                    )
                ),
                name="evenement_mortalite_perte_referentiel_valide",
            ),
        ),
        migrations.AddConstraint(
            model_name="evenementmortaliteperte",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(("statut", "ANNULEE"))
                    | models.Q(("motif_annulation__gt", ""))
                ),
                name="evenement_mortalite_perte_annulation_motivee",
            ),
        ),
        migrations.RemoveIndex(
            model_name="evenementmortaliteperte",
            name="evt_mort_perte_periode_idx",
        ),
        migrations.AddIndex(
            model_name="evenementmortaliteperte",
            index=models.Index(
                fields=["bassin", "hapa", "date_evenement"],
                name="evt_mort_perte_hapa_date_idx",
            ),
        ),
        migrations.RunPython(
            initialiser_referentiels,
            migrations.RunPython.noop,
        ),
        migrations.AlterModelOptions(
            name="evenementmortaliteperte",
            options={
                "ordering": ["-date_evenement", "-id"],
                "verbose_name": "Événement de mortalité/perte",
                "verbose_name_plural": "Événements de mortalité/perte",
            },
        ),
    ]
