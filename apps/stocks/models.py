
from django.conf import settings
from django.db import models
from django.utils import timezone


class MiseEnStock(models.Model):
    """Enregistrement de l'entrée physique d'un lot dans un bassin de stock."""

    class Finalite(models.TextChoices):
        GROSSISSEMENT = "GROSSISSEMENT", "Grossissement"
        VENTE = "VENTE", "Vente"

    lot = models.OneToOneField(
        "lots.Lot",
        on_delete=models.PROTECT,
        related_name="mise_en_stock",
    )

    bassin = models.ForeignKey(
        "bassins.Bassin",
        on_delete=models.PROTECT,
        related_name="mises_en_stock",
    )

    finalite = models.CharField(
        max_length=20,
        choices=Finalite.choices,
        null=True,
        blank=True,
    )

    # Total des lignes, calculé par le service.
    effectif_entrant = models.PositiveIntegerField()

    # La date de l'événement peut être antérieure à sa saisie.
    date_mise_en_stock = models.DateTimeField(default=timezone.now)

    # L'utilisateur est fourni par le backend, jamais par le client.
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="mises_en_stock_enregistrees",
    )

    # Obligatoire lorsqu'un écart d'effectif doit être expliqué.
    justification_ecart = models.TextField(blank=True, default="")

    observation = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_mise_en_stock", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effectif_entrant__gt=0),
                name="mise_en_stock_effectif_entrant_positif",
            ),
        ]

    @property
    def ecart_effectif(self):
        """Écart entre l'effectif entrant et l'effectif final du traitement."""
        # Sans traitement clôturé, cet écart n'est pas applicable.
        traitement = getattr(self.lot, "traitement", None)

        if (
            traitement is None
            or traitement.effectif_final is None
        ):
            return None

        return self.effectif_entrant - traitement.effectif_final

    def __str__(self):
        return f"Mise en stock du lot {self.lot.reference}"


class MiseEnStockHapa(models.Model):
    mise_en_stock = models.ForeignKey(
        MiseEnStock,
        on_delete=models.PROTECT,
        related_name="lignes_hapas",
    )
    hapa = models.ForeignKey(
        "bassins.Hapa",
        on_delete=models.PROTECT,
        related_name="lignes_mise_en_stock",
    )
    effectif_entrant = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["mise_en_stock", "hapa"],
                name="unique_mise_en_stock_hapa",
            ),
            models.CheckConstraint(
                condition=models.Q(effectif_entrant__gt=0),
                name="mise_en_stock_hapa_effectif_positif",
            ),
        ]
