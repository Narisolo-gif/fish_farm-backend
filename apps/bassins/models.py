from django import utils
from django.db import models


class Bassin(models.Model):
    class Categorie(models.TextChoices):
        REPRODUCTION = "REPRODUCTION", "Reproduction"
        ECLOSERIE = "ECLOSERIE", "Écloserie"
        TRAITEMENT = "TRAITEMENT", "Traitement"
        STOCKS = "STOCKS", "Stocks"
        GROSSISSEMENT = "GROSSISSEMENT", "Grossissement"

    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        INACTIF = "INACTIF", "Inactif"

    reference = models.CharField(
        max_length=100,
        unique=True,
    )

    categorie = models.CharField(
        max_length=20,
        choices=Categorie.choices,
    )

    capacity = models.PositiveIntegerField()

    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.ACTIF,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class Hapa(models.Model):
    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        INACTIF = "INACTIF", "Inactif"

    reference = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
    )

    bassin = models.ForeignKey(
        Bassin,
        on_delete=models.PROTECT,
        related_name="hapas",
    )

    capacity = models.PositiveIntegerField(null=True, blank=True)

    nom = models.CharField(max_length=100)
    actif = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.ACTIF,
    )

    class Meta:
        ordering = ["nom"]
        constraints = [
            models.UniqueConstraint(
                fields=["bassin", "nom"],
                name="hapa_bassin_nom_unique",
            ),
        ]

    def __str__(self):
        return self.reference or self.nom