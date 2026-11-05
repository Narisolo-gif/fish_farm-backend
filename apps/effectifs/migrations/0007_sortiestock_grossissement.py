import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def detacher_mouvements_legacy(apps, schema_editor):
    MouvementEffectif = apps.get_model("effectifs", "MouvementEffectif")
    ContentType = apps.get_model("contenttypes", "ContentType")
    content_type = ContentType.objects.filter(
        app_label="effectifs",
        model="sortiestock",
    ).first()
    if content_type is None:
        return
    MouvementEffectif.objects.filter(
        operation_source_content_type_id=content_type.pk,
    ).update(
        operation_source_content_type=None,
        operation_source_object_id=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("bassins", "0006_hapa_created_at"),
        ("effectifs", "0006_blocages_journal_evenements"),
        ("lots", "0002_lot_parcours_production"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            detacher_mouvements_legacy,
            migrations.RunPython.noop,
        ),
        migrations.DeleteModel(
            name="SortieStockComposition",
        ),
        migrations.DeleteModel(
            name="SortieStock",
        ),
        migrations.CreateModel(
            name="SortieStock",
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
                (
                    "type_sortie",
                    models.CharField(
                        choices=[("GROSSISSEMENT", "Grossissement")],
                        default="GROSSISSEMENT",
                        max_length=20,
                    ),
                ),
                (
                    "espece",
                    models.CharField(
                        choices=[("CARPE", "Carpe"), ("TILAPIA", "Tilapia")],
                        max_length=20,
                    ),
                ),
                (
                    "date_sortie",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "statut",
                    models.CharField(
                        choices=[
                            ("BROUILLON", "Brouillon"),
                            ("VALIDEE", "Validée"),
                            ("ANNULEE", "Annulée"),
                        ],
                        default="BROUILLON",
                        max_length=10,
                    ),
                ),
                ("observation", models.TextField(blank=True)),
                ("effectif_sortant", models.PositiveIntegerField(blank=True, null=True)),
                ("effectif_entrant", models.PositiveIntegerField(blank=True, null=True)),
                ("justification_ecart", models.TextField(blank=True, default="")),
                ("validee_le", models.DateTimeField(blank=True, null=True)),
                ("annulee_le", models.DateTimeField(blank=True, null=True)),
                ("motif_annulation", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "annulee_par",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sorties_stock_annulees",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "bassin_destination",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sorties_stock_destination",
                        to="bassins.bassin",
                    ),
                ),
                (
                    "lot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sorties_stock",
                        to="lots.lot",
                    ),
                ),
                (
                    "utilisateur",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sorties_stock_creees",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "validee_par",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sorties_stock_validees",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-date_sortie", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="sortiestock",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("effectif_sortant__isnull", True))
                    | models.Q(("effectif_entrant__isnull", True))
                    | models.Q(
                        ("effectif_entrant__lte", models.F("effectif_sortant"))
                    )
                ),
                name="sortie_stock_entrant_lte_sortant",
            ),
        ),
        migrations.AddConstraint(
            model_name="sortiestock",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        models.Q(("statut", "VALIDEE"), _negated=True),
                        models.Q(
                            ("effectif_entrant__isnull", False),
                            ("effectif_sortant__isnull", False),
                        ),
                        _connector="OR",
                    )
                ),
                name="sortie_stock_validee_effectifs_requis",
            ),
        ),
        migrations.AddConstraint(
            model_name="sortiestock",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("statut", "ANNULEE"), _negated=True)
                    | models.Q(("motif_annulation__gt", ""))
                ),
                name="sortie_stock_annulation_motivee",
            ),
        ),
        migrations.AddConstraint(
            model_name="sortiestock",
            constraint=models.UniqueConstraint(
                condition=models.Q(("statut__in", ["BROUILLON", "VALIDEE"])),
                fields=("lot",),
                name="sortie_stock_lot_active_unique",
            ),
        ),
        migrations.CreateModel(
            name="SortieStockLigne",
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
                ("quantite", models.PositiveIntegerField()),
                (
                    "hapa",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="lignes_sortie_stock",
                        to="bassins.hapa",
                    ),
                ),
                (
                    "sortie",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="lignes",
                        to="effectifs.sortiestock",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="sortiestockligne",
            constraint=models.CheckConstraint(
                condition=models.Q(("quantite__gt", 0)),
                name="sortie_stock_ligne_quantite_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="sortiestockligne",
            constraint=models.UniqueConstraint(
                fields=("sortie", "hapa"),
                name="sortie_stock_ligne_hapa_unique",
            ),
        ),
    ]
