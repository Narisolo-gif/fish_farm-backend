from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.bassins.models import Bassin, Hapa
from apps.lots.models import Lot


class MouvementEffectif(models.Model):
    """Journal des mouvements d’effectif par bassin et espèce."""

    class TypeMouvement(models.TextChoices):
        ENTREE = "ENTREE", "Entrée"
        SORTIE = "SORTIE", "Sortie"
        MORTALITE = "MORTALITE", "Mortalité"
        PERTE = "PERTE", "Perte"
        AJUSTEMENT_POSITIF = "AJUSTEMENT_POSITIF", "Ajustement positif"
        VENTE = "VENTE", "Vente"
        ANNULATION = "ANNULATION", "Annulation"

    class Sens(models.IntegerChoices):
        POSITIF = 1, "Positif"
        NEGATIF = -1, "Négatif"

    bassin = models.ForeignKey(
        Bassin,
        on_delete=models.PROTECT,
        related_name="mouvements_effectif",
    )
    espece = models.CharField(
        max_length=20,
        choices=Lot.Espece.choices,
    )
    type_mouvement = models.CharField(
        max_length=30,
        choices=TypeMouvement.choices,
    )
    sens = models.SmallIntegerField(
        choices=Sens.choices,
    )
    quantite = models.PositiveIntegerField()
    date_evenement = models.DateField()

    lot_origine = models.ForeignKey(
        Lot,
        on_delete=models.PROTECT,
        related_name="mouvements_effectif_origine",
        null=True,
        blank=True,
    )
    hapa = models.ForeignKey(
        Hapa,
        on_delete=models.PROTECT,
        related_name="mouvements_effectif",
        null=True,
        blank=True,
    )
    lot_grossissement = models.ForeignKey(
        Lot,
        on_delete=models.PROTECT,
        related_name="mouvements_effectif_grossissement",
        null=True,
        blank=True,
    )

    operation_source_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="mouvements_effectif_source",
    )
    operation_source_object_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
    )
    operation_source = GenericForeignKey(
        "operation_source_content_type",
        "operation_source_object_id",
    )

    annule_mouvement = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="annulations_associees",
    )
    motif_annulation = models.TextField(
        blank=True,
        default="",
    )
    saisi_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="mouvements_effectif",
    )
    saisi_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_evenement", "-saisi_le", "-id"]
        constraints = [
            # Ces contraintes protègent aussi les écritures qui ne passent pas par le service.
            models.CheckConstraint(
                check=models.Q(quantite__gt=0),
                name="mouvement_effectif_quantite_positive",
            ),
            models.CheckConstraint(
                check=models.Q(sens__in=[1, -1]),
                name="mouvement_effectif_sens_valide",
            ),
            models.CheckConstraint(
                check=(
                    ~models.Q(type_mouvement="ANNULATION")
                    | models.Q(annule_mouvement__isnull=False)
                ),
                name="mouvement_effectif_annulation_reliee",
            ),
            models.UniqueConstraint(
                fields=["annule_mouvement"],
                condition=models.Q(annule_mouvement__isnull=False),
                name="mouvement_effectif_annulation_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "bassin",
                    "espece",
                    "date_evenement",
                    "saisi_le",
                    "id",
                ],
                name="mvt_eff_solde_idx",
            ),
            models.Index(
                fields=[
                    "operation_source_content_type",
                    "operation_source_object_id",
                ],
                name="mvt_eff_source_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.type_mouvement} / {self.bassin.reference} / "
            f"{self.espece} / {self.quantite}"
        )

    def save(self, *args, **kwargs):
        # Un mouvement existant se corrige uniquement par un contre-mouvement lié.
        if self.pk:
            raise ValidationError(
                "Un mouvement d’effectif est immuable ; annulez-le via le service dédié."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "La suppression d’un mouvement d’effectif est interdite."
        )


class CauseMortalite(models.Model):
    libelle = models.CharField(max_length=100, unique=True)
    actif = models.BooleanField(default=True)

    class Meta:
        ordering = ["libelle"]
        verbose_name = "Cause de mortalité"
        verbose_name_plural = "Causes de mortalité"

    def __str__(self):
        return self.libelle


class CategoriePerte(models.Model):
    libelle = models.CharField(max_length=100, unique=True)
    actif = models.BooleanField(default=True)

    class Meta:
        ordering = ["libelle"]
        verbose_name = "Catégorie de perte"
        verbose_name_plural = "Catégories de perte"

    def __str__(self):
        return self.libelle


class BlocageSaisie(models.Model):
    hapa = models.ForeignKey(
        Hapa,
        on_delete=models.PROTECT,
        related_name="blocages_saisie",
    )
    operation_type = models.CharField(max_length=100)
    operation_id = models.PositiveIntegerField()
    motif = models.TextField()
    actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    leve_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hapa", "operation_type", "operation_id"],
                condition=models.Q(actif=True),
                name="blocage_saisie_hapa_operation_actif_unique",
            ),
        ]


class EvenementMortalitePerte(models.Model):
    class TypeEvenement(models.TextChoices):
        MORTALITE = "MORTALITE", "Mortalité"
        PERTE = "PERTE", "Perte"

    class Statut(models.TextChoices):
        VALIDE = "VALIDE", "Validé"
        ANNULEE = "ANNULEE", "Annulé"

    bassin = models.ForeignKey(
        Bassin,
        on_delete=models.PROTECT,
        related_name="evenements_mortalite_perte",
    )
    hapa = models.ForeignKey(
        Hapa,
        on_delete=models.PROTECT,
        related_name="evenements_mortalite_perte",
        null=True,
        blank=True,
    )
    espece = models.CharField(max_length=20, choices=Lot.Espece.choices)
    type_evenement = models.CharField(
        max_length=20,
        choices=TypeEvenement.choices,
    )
    quantite = models.PositiveIntegerField()
    date_evenement = models.DateTimeField(default=timezone.now)
    cause_mortalite = models.ForeignKey(
        CauseMortalite,
        on_delete=models.PROTECT,
        related_name="evenements",
        null=True,
        blank=True,
    )
    categorie_perte = models.ForeignKey(
        CategoriePerte,
        on_delete=models.PROTECT,
        related_name="evenements",
        null=True,
        blank=True,
    )
    justification = models.TextField(blank=True, default="")
    observation = models.TextField(blank=True, default="")
    motif_annulation = models.TextField(blank=True, default="")
    annulee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="evenements_mortalite_perte_annules",
        null=True,
        blank=True,
    )
    annulee_le = models.DateTimeField(null=True, blank=True)
    mouvement = models.OneToOneField(
        MouvementEffectif,
        on_delete=models.PROTECT,
        related_name="evenement_mortalite_perte",
        null=True,
        blank=True,
    )
    statut = models.CharField(
        max_length=10,
        choices=Statut.choices,
        default=Statut.VALIDE,
    )
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="evenements_mortalite_perte_crees",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_evenement", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantite__gt=0),
                name="evenement_mortalite_perte_quantite_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        type_evenement="MORTALITE",
                        cause_mortalite__isnull=False,
                        categorie_perte__isnull=True,
                    )
                    | models.Q(
                        type_evenement="PERTE",
                        cause_mortalite__isnull=True,
                        categorie_perte__isnull=False,
                        justification__gt="",
                    )
                ),
                name="evenement_mortalite_perte_referentiel_valide",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(statut="ANNULEE")
                    | models.Q(motif_annulation__gt="")
                ),
                name="evenement_mortalite_perte_annulation_motivee",
            ),
        ]
        indexes = [
            models.Index(
                fields=["bassin", "hapa", "date_evenement"],
                name="evt_mort_perte_hapa_date_idx",
            ),
        ]
        verbose_name = "Événement de mortalité/perte"
        verbose_name_plural = "Événements de mortalité/perte"

    def __str__(self):
        return (
            f"{self.get_type_evenement_display()} / {self.bassin.reference} / "
            f"{self.espece} / {self.quantite}"
        )

    def save(self, *args, **kwargs):
        # L'événement est immuable après création ; seul son statut peut être annulé par service.
        if self.pk:
            raise ValidationError(
                "Un événement existant ne peut pas être modifié."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "La suppression d’un événement de mortalité/perte est interdite."
        )


class SortieStock(models.Model):
    class TypeSortie(models.TextChoices):
        GROSSISSEMENT = "GROSSISSEMENT", "Grossissement"

    class Statut(models.TextChoices):
        BROUILLON = "BROUILLON", "Brouillon"
        VALIDEE = "VALIDEE", "Validée"
        ANNULEE = "ANNULEE", "Annulée"

    type_sortie = models.CharField(
        max_length=20,
        choices=TypeSortie.choices,
        default=TypeSortie.GROSSISSEMENT,
    )
    lot = models.ForeignKey(
        Lot,
        on_delete=models.PROTECT,
        related_name="sorties_stock",
    )
    bassin_destination = models.ForeignKey(
        Bassin,
        on_delete=models.PROTECT,
        related_name="sorties_stock_destination",
    )
    espece = models.CharField(max_length=20, choices=Lot.Espece.choices)
    date_sortie = models.DateTimeField(default=timezone.now)
    statut = models.CharField(
        max_length=10,
        choices=Statut.choices,
        default=Statut.BROUILLON,
    )
    observation = models.TextField(blank=True)
    effectif_sortant = models.PositiveIntegerField(null=True, blank=True)
    effectif_entrant = models.PositiveIntegerField(null=True, blank=True)
    justification_ecart = models.TextField(blank=True, default="")
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sorties_stock_creees",
    )
    validee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sorties_stock_validees",
        null=True,
        blank=True,
    )
    validee_le = models.DateTimeField(null=True, blank=True)
    annulee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sorties_stock_annulees",
        null=True,
        blank=True,
    )
    annulee_le = models.DateTimeField(null=True, blank=True)
    motif_annulation = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_sortie", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(effectif_sortant__isnull=True)
                    | models.Q(effectif_entrant__isnull=True)
                    | models.Q(effectif_entrant__lte=models.F("effectif_sortant"))
                ),
                name="sortie_stock_entrant_lte_sortant",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(statut="VALIDEE")
                    | models.Q(
                        effectif_sortant__isnull=False,
                        effectif_entrant__isnull=False,
                    )
                ),
                name="sortie_stock_validee_effectifs_requis",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(statut="ANNULEE")
                    | models.Q(motif_annulation__gt="")
                ),
                name="sortie_stock_annulation_motivee",
            ),
            models.UniqueConstraint(
                fields=["lot"],
                condition=models.Q(statut__in=["BROUILLON", "VALIDEE"]),
                name="sortie_stock_lot_active_unique",
            ),
        ]

    @property
    def ecart_effectif(self):
        if self.effectif_sortant is None or self.effectif_entrant is None:
            return None
        return self.effectif_entrant - self.effectif_sortant

    def __str__(self):
        return f"{self.get_type_sortie_display()} / {self.lot.reference}"


class SortieStockLigne(models.Model):
    sortie = models.ForeignKey(
        SortieStock,
        on_delete=models.PROTECT,
        related_name="lignes",
    )
    hapa = models.ForeignKey(
        Hapa,
        on_delete=models.PROTECT,
        related_name="lignes_sortie_stock",
    )
    quantite = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantite__gt=0),
                name="sortie_stock_ligne_quantite_positive",
            ),
            models.UniqueConstraint(
                fields=["sortie", "hapa"],
                name="sortie_stock_ligne_hapa_unique",
            ),
        ]
