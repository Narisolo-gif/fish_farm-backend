from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.bassins.models import Bassin
from apps.effectifs.models import MouvementEffectif, SortieStock
from apps.lots.models import Lot


CYCLE_PLAN_JOURS = [30, 30] + [15] * 8


class LotGrossissement(models.Model):
    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        CLOTURE = "CLOTURE", "Clôturé"

    code = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        editable=False,
    )
    lot_origine = models.ForeignKey(
        Lot,
        on_delete=models.PROTECT,
        related_name="lots_grossissement",
    )
    sortie_stock = models.OneToOneField(
        SortieStock,
        on_delete=models.PROTECT,
        related_name="lot_grossissement",
    )
    bassin = models.ForeignKey(
        Bassin,
        on_delete=models.PROTECT,
        related_name="lots_grossissement",
    )
    espece = models.CharField(
        max_length=20,
        choices=MouvementEffectif._meta.get_field("espece").choices,
    )
    date_mise_en_place = models.DateTimeField()
    effectif_initial = models.PositiveIntegerField()
    poids_initial_g = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    pourcentage_groupe_pesee = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=10,
    )
    statut = models.CharField(
        max_length=10,
        choices=Statut.choices,
        default=Statut.ACTIF,
    )
    date_cloture = models.DateField(null=True, blank=True)
    # Effectif vivant au jour de la clôture, lu dans le registre.
    effectif_final = models.PositiveIntegerField(null=True, blank=True)
    poids_final_g = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    motif_cloture = models.TextField(blank=True, default="")
    observation = models.TextField(blank=True, default="")
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lots_grossissement_crees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["bassin", "statut"], name="lot_gross_bassin_statut_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effectif_initial__gt=0),
                name="lot_gross_effectif_initial_positif",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(poids_initial_g__isnull=True)
                    | models.Q(poids_initial_g__gt=0)
                ),
                name="lot_gross_poids_initial_positif",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(poids_final_g__isnull=True)
                    | models.Q(poids_final_g__gt=0)
                ),
                name="lot_gross_poids_final_positif",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(pourcentage_groupe_pesee__gt=0)
                    & models.Q(pourcentage_groupe_pesee__lte=100)
                ),
                name="lot_gross_pourcentage_valide",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(statut="CLOTURE")
                    | models.Q(
                        date_cloture__isnull=False,
                        motif_cloture__gt="",
                    )
                ),
                name="lot_gross_cloture_motivee",
            ),
            models.UniqueConstraint(
                fields=["lot_origine"],
                name="lot_gross_origine_unique",
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.code = f"TMP-{uuid4().hex[:16]}"
            super().save(*args, **kwargs)
            annee = timezone.localtime(self.date_mise_en_place).year
            self.code = f"GR-{annee}-{self.pk:04d}"
            super().save(update_fields=["code"])
            return
        super().save(*args, **kwargs)

    @property
    def age_jours(self):
        date_mise_en_place = timezone.localtime(
            self.date_mise_en_place
        ).date()
        return max(0, (timezone.localdate() - date_mise_en_place).days)

    @property
    def etape_courante(self):
        age = self.age_jours
        cumul = 0
        for etape, duree in enumerate(CYCLE_PLAN_JOURS, start=1):
            cumul += duree
            if age < cumul:
                return etape
        return None

    @property
    def prochaine_echeance_pesee(self):
        age = self.age_jours
        if age >= sum(CYCLE_PLAN_JOURS):
            return None
        date_mise_en_place = timezone.localtime(
            self.date_mise_en_place
        ).date()
        cumul = 0
        for duree in CYCLE_PLAN_JOURS:
            cumul += duree
            if cumul >= age:
                return date_mise_en_place + timedelta(days=cumul)
        return None

    def __str__(self):
        return self.code or f"Lot de grossissement {self.pk}"