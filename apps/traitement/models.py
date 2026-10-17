from django.conf import settings
from django.db import models

from apps.bassins.models import Bassin
from apps.lots.models import Lot
from apps.provende.models import Provende


class Traitement(models.Model):
    """Traitement appliqué à un lot de poissons."""

    class Statut(models.TextChoices):
        EN_COURS = "EN_COURS", "En cours"
        CLOTUREE = "CLOTUREE", "Clôturée"

    # Un lot ne peut avoir qu'un seul traitement enregistré à la fois.
    lot = models.OneToOneField(
        Lot,
        on_delete=models.PROTECT,
        related_name="traitement",
    )

    # L'effectif initial doit être strictement positif.
    effectif_initial = models.PositiveIntegerField()

    # Saisi exclusivement lors de la clôture ; zéro reste autorisé.
    effectif_final = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    date_ouverture = models.DateField()

    # Renseignée automatiquement par le service de clôture.
    date_cloture = models.DateField(
        null=True,
        blank=True,
    )

    # Le bassin doit être actif et de catégorie TRAITEMENT.
    bassin = models.ForeignKey(
        Bassin,
        on_delete=models.PROTECT,
        related_name="traitements",
    )

    used_provende = models.ForeignKey(
        Provende,
        on_delete=models.PROTECT,
        related_name="traitements",
    )

    # Défini automatiquement à partir de l'utilisateur authentifié.
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="traitements_crees",
    )

    # La transition de statut est gérée par le backend.
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.EN_COURS,
    )
    # Observation facultative concernant le traitement.
    observation = models.TextField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_ouverture", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effectif_initial__gt=0),
                name="traitement_effectif_initial_positif",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(effectif_final__isnull=True)
                    | models.Q(
                        effectif_final__lte=models.F("effectif_initial")
                    )
                ),
                name="traitement_effectif_final_max_initial",
            ),
        ]

    def __str__(self):
        return f"Traitement du lot {self.lot.reference}"