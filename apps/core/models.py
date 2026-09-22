from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.Model):
    libelle = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["libelle"]

    def __str__(self):
        return self.libelle


class Utilisateur(AbstractUser):
    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        INACTIF = "INACTIF", "Inactif"

    role = models.ForeignKey(
        Role,
        on_delete=models.PROTECT,
        related_name="utilisateurs",
        null=True,
        blank=True,
    )

    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.ACTIF,
    )

    must_change_password = models.BooleanField(
        default=True,
    )

    def __str__(self):
        return self.username