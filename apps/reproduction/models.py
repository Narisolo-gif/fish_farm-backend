from django.db import models
from django.db.models import Q

from apps.bassins.models import Bassin


class Reproduction(models.Model):
    class Espece(models.TextChoices):
        CARPE = "CARPE", "Carpe"
        TILAPIA = "TILAPIA", "Tilapia"

    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        INACTIF = "INACTIF", "Inactif"

    reference = models.CharField(
        max_length=100,
        unique=True,
    )

    bassin = models.ForeignKey(
        Bassin,
        on_delete=models.PROTECT,
        related_name="reproductions",
    )

    espece = models.CharField(
        max_length=20,
        choices=Espece.choices,
    )

    date_ajout = models.DateField()

    quantite_male = models.PositiveIntegerField()

    quantite_femelle = models.PositiveIntegerField()

    observation = models.TextField(
        blank=True,
    )

    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.ACTIF,
    )

    class Meta:
        ordering = ["-date_ajout", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["bassin"],
                condition=Q(statut="ACTIF"),
                name="unique_reproduction_active_par_bassin",
            ),
        ]

    def __str__(self):
        return self.reference


class Ponte(models.Model):
    reference = models.CharField(
        max_length=100,
        unique=True,
    )

    reproduction = models.ForeignKey(
        Reproduction,
        on_delete=models.CASCADE,
        related_name="pontes",
    )

    date_ponte = models.DateField()

    quantite_oeuf_obtenu = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    observation = models.TextField(
        blank=True,
    )

    class Meta:
        ordering = ["-date_ponte", "-id"]

    def __str__(self):
        return self.reference