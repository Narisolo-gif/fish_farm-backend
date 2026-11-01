
from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.reproduction.models import Ponte


class Eclosion(models.Model):
    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        INACTIF = "INACTIF", "Inactif"

    reference = models.CharField(
        max_length=100,
        unique=True,
    )
    ponte = models.ForeignKey(
        Ponte,
        on_delete=models.PROTECT,
        related_name="eclosions",
    )
    date_eclosion = models.DateField()
    quantite_oeufs_eclos = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    quantite_alevins_obtenu = models.PositiveIntegerField()
    observation = models.TextField(
        null=True,
        blank=True,
    )
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.ACTIF,
    )

    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="eclosions_creees",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_eclosion", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(quantite_oeufs_eclos__isnull=True)
                    | Q(quantite_oeufs_eclos__gt=0)
                ),
                name="eclosion_quantite_oeufs_positive_ou_nulle",
            ),
            models.CheckConstraint(
                condition=Q(quantite_alevins_obtenu__gt=0),
                name="eclosion_quantite_alevins_positive",
            ),
        ]

    def __str__(self):
        return self.reference
