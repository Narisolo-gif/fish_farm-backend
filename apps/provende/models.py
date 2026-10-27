from django.conf import settings
from django.db import models

from apps.bassins.models import Hapa


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


class ConsommationProvende(models.Model):
    class Statut(models.TextChoices):
        VALIDE = "VALIDE", "Valide"
        ANNULEE = "ANNULEE", "Annulée"

    bassin = models.ForeignKey(
        "bassins.Bassin",
        on_delete=models.PROTECT,
        related_name="consommations_provende",
    )
    hapa = models.ForeignKey(
        Hapa,
        on_delete=models.PROTECT,
        related_name="consommations_provende",
        null=True,
        blank=True,
    )
    provende = models.ForeignKey(
        Provende,
        on_delete=models.PROTECT,
        related_name="consommations",
    )
    date_distribution = models.DateTimeField()
    quantite_kg = models.DecimalField(max_digits=8, decimal_places=2)
    observation = models.TextField(blank=True)
    statut = models.CharField(
        max_length=10,
        choices=Statut.choices,
        default=Statut.VALIDE,
    )
    motif_annulation = models.TextField(blank=True)
    annulee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="consommations_provende_annulees",
        null=True,
        blank=True,
    )
    annulee_le = models.DateTimeField(null=True, blank=True)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="consommations_provende_saisies",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_distribution", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantite_kg__gte=0),
                name="consommation_provende_quantite_nonnegative",
            ),
            models.UniqueConstraint(
                fields=["bassin", "provende", "date_distribution"],
                condition=models.Q(statut="VALIDE", hapa__isnull=True),
                name="consommation_provende_unique_valide_sans_hapa",
            ),
            models.UniqueConstraint(
                fields=["bassin", "hapa", "provende", "date_distribution"],
                condition=models.Q(statut="VALIDE", hapa__isnull=False),
                name="consommation_provende_unique_valide_avec_hapa",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(statut="ANNULEE")
                    | models.Q(motif_annulation__gt="")
                ),
                name="consommation_provende_annulation_motivee",
            ),
        ]

    def __str__(self):
        return (
            f"{self.provende.reference} / {self.bassin.reference} / "
            f"{self.date_distribution:%Y-%m-%d %H:%M}"
        )