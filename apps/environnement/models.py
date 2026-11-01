from django.conf import settings
from django.db import models

from apps.bassins.models import Bassin


class ParametreEnvironnemental(models.Model):
    """
    Enregistre les paramètres environnementaux mesurés
    lors d'un prélèvement effectué sur un bassin.
    """

    bassin = models.ForeignKey(
        Bassin,
        on_delete=models.PROTECT,
        related_name="parametres_environnementaux",
    )

    date_heure_prelevement = models.DateTimeField()

    temperature_eau = models.DecimalField(
        max_digits=6,
        decimal_places=2,
    )

    temperature_air = models.DecimalField(
        max_digits=6,
        decimal_places=2,
    )

    oxygene = models.DecimalField(
        max_digits=6,
        decimal_places=2,
    )

    ph = models.DecimalField(
        max_digits=4,
        decimal_places=2,
    )

    nitrite = models.DecimalField(
        max_digits=6,
        decimal_places=2,
    )

    nitrate = models.DecimalField(
        max_digits=6,
        decimal_places=2,
    )

    ammoniac = models.DecimalField(
        max_digits=6,
        decimal_places=2,
    )

    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="parametres_environnementaux_crees",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_heure_prelevement"]

        indexes = [
            models.Index(
                fields=["bassin", "-date_heure_prelevement"]
            ),
        ]

    def __str__(self):
        return (
            f"{self.bassin.reference} - "
            f"{self.date_heure_prelevement}"
        )