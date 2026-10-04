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

    class Meta:
        ordering = ["reference"]

    def __str__(self):
        return self.reference


class Hapa(models.Model):
    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        INACTIF = "INACTIF", "Inactif"

    reference = models.CharField(
        max_length=100,
        unique=True,
    )

    bassin = models.ForeignKey(
        Bassin,
        on_delete=models.CASCADE,
        related_name="hapas",
    )

    capacity = models.PositiveIntegerField()

    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.ACTIF,
    )

    class Meta:
        ordering = ["reference"]

    def __str__(self):
        return self.reference