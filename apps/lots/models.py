
from django.conf import settings
from django.db import models

from apps.ecloserie.models import Eclosion


class Lot(models.Model):
    class Espece(models.TextChoices):
        CARPE = "CARPE", "Carpe"
        TILAPIA = "TILAPIA", "Tilapia"

    class Stade(models.TextChoices):
        CREEE = "CREEE", "Créée"
        EN_TRAITEMENT = "EN_TRAITEMENT", "En traitement"
        EN_STOCK = "EN_STOCK", "En stock"
        CLOTUREE = "CLOTUREE", "Clôturée"

    # Définit le parcours de production choisi pour le lot.
    class ParcoursProduction(models.TextChoices):
        TRAITEMENT_REQUIS = "TRAITEMENT_REQUIS", "Traitement requis"
        SANS_TRAITEMENT = "SANS_TRAITEMENT", "Sans traitement"

    reference = models.CharField(
        max_length=100,
        unique=True,
    )
    eclosion = models.OneToOneField(
        Eclosion,
        on_delete=models.PROTECT,
        related_name="lot",
    )
    date_creation = models.DateTimeField()
    espece = models.CharField(
        max_length=20,
        choices=Espece.choices,
    )
    utilisateur_createur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lots_crees",
    )
    quantite_initiale = models.PositiveIntegerField()

    # Valeur par défaut compatible avec les créations de lots existantes.
    parcours_production = models.CharField(
        max_length=30,
        choices=ParcoursProduction.choices,
        default=ParcoursProduction.TRAITEMENT_REQUIS,
    )

    stade = models.CharField(
        max_length=20,
        choices=Stade.choices,
        default=Stade.CREEE,
    )
    observations = models.TextField(
        blank=True,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-date_creation", "-id"]

    def __str__(self):
        return self.reference
