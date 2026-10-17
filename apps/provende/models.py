from django.db import models


class TypeProvende(models.Model):
    """Type de provende utilisable dans l'exploitation."""

    class Type(models.TextChoices):
        TRAITEMENT = "TRAITEMENT", "Traitement"
        DEMARRAGE = "DEMARRAGE", "Démarrage"
        CROISSANCE = "CROISSANCE", "Croissance"
        FINITION = "FINITION", "Finition"
        GENITEURS = "GENITEURS", "Géniteurs"

    # Les valeurs sont limitées aux cinq types métier définis.
    type = models.CharField(
        max_length=20,
        choices=Type.choices,
        unique=True,
    )

    class Meta:
        ordering = ["type"]
        verbose_name = "Type de provende"
        verbose_name_plural = "Types de provende"

    def __str__(self):
        return self.get_type_display()


class Provende(models.Model):
    """Référence d'une provende enregistrée dans le système."""

    type_provende = models.ForeignKey(
        TypeProvende,
        on_delete=models.PROTECT,  # Protège les types encore utilisés.
        related_name="provendes",
    )

    # La référence est saisie manuellement et doit être unique.
    reference = models.CharField(
        max_length=100,
        unique=True,
    )

    # Date fournie par l'utilisateur, distincte de created_at.
    date_creation = models.DateField()

    # Champ facultatif.
    remarques = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_creation", "reference"]

    def __str__(self):
        return self.reference