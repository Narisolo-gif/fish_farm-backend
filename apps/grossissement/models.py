from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.bassins.models import Bassin
from apps.effectifs.models import MouvementEffectif, SortieStock
from apps.lots.models import Lot

from .calculs import etat_calendrier


class LotGrossissement(models.Model):
    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        CLOTURE = "CLOTURE", "Clôturé"

    code = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        editable=False,
    )
    lot_origine = models.ForeignKey(
        Lot,
        on_delete=models.PROTECT,
        related_name="lots_grossissement",
    )
    sortie_stock = models.OneToOneField(
        SortieStock,
        on_delete=models.PROTECT,
        related_name="lot_grossissement",
    )
    bassin = models.ForeignKey(
        Bassin,
        on_delete=models.PROTECT,
        related_name="lots_grossissement",
    )
    espece = models.CharField(
        max_length=20,
        choices=MouvementEffectif._meta.get_field("espece").choices,
    )
    date_mise_en_place = models.DateTimeField()
    effectif_initial = models.PositiveIntegerField()
    # Poids moyen (g) mesuré à la pesée initiale P0 ; point de départ des indicateurs.
    poids_moyen_initial = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    pourcentage_groupe_pesee = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=10,
    )
    # Trace de la pesée initiale (P0), faite avant la mise en bassin. Seule la
    # validation de la sortie les renseigne ; nullables pour les lots antérieurs.
    date_pesee_initiale = models.DateField(null=True, blank=True)
    pourcentage_echantillon_initial = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    nombre_poissons_peses_initial = models.PositiveIntegerField(
        null=True,
        blank=True,
    )
    poids_total_initial_g = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    statut = models.CharField(
        max_length=10,
        choices=Statut.choices,
        default=Statut.ACTIF,
    )
    date_cloture = models.DateField(null=True, blank=True)
    # Effectif vivant au jour de la clôture, lu dans le registre.
    effectif_final = models.PositiveIntegerField(null=True, blank=True)
    poids_final_g = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    motif_cloture = models.TextField(blank=True, default="")
    observation = models.TextField(blank=True, default="")
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lots_grossissement_crees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["bassin", "statut"], name="lot_gross_bassin_statut_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effectif_initial__gt=0),
                name="lot_gross_effectif_initial_positif",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(poids_moyen_initial__isnull=True)
                    | models.Q(poids_moyen_initial__gt=0)
                ),
                name="lot_gross_poids_moyen_initial_positif",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(poids_final_g__isnull=True)
                    | models.Q(poids_final_g__gt=0)
                ),
                name="lot_gross_poids_final_positif",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(pourcentage_groupe_pesee__gt=0)
                    & models.Q(pourcentage_groupe_pesee__lte=100)
                ),
                name="lot_gross_pourcentage_valide",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(statut="CLOTURE")
                    | models.Q(
                        date_cloture__isnull=False,
                        motif_cloture__gt="",
                    )
                ),
                name="lot_gross_cloture_motivee",
            ),
            models.UniqueConstraint(
                fields=["lot_origine"],
                name="lot_gross_origine_unique",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(pourcentage_echantillon_initial__isnull=True)
                    | (
                        models.Q(pourcentage_echantillon_initial__gt=0)
                        & models.Q(pourcentage_echantillon_initial__lte=100)
                    )
                ),
                name="lot_gross_pourcentage_initial_valide",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(nombre_poissons_peses_initial__isnull=True)
                    | models.Q(nombre_poissons_peses_initial__gt=0)
                ),
                name="lot_gross_nombre_peses_initial_positif",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(poids_total_initial_g__isnull=True)
                    | models.Q(poids_total_initial_g__gt=0)
                ),
                name="lot_gross_poids_total_initial_positif",
            ),
        ]

    def save(self, *args, **kwargs):
        # Sans code fourni (référence saisie à la validation de la sortie), le
        # code est généré au format GR-AAAA-NNNN à partir de l'identifiant.
        if self._state.adding and not self.code:
            self.code = f"TMP-{uuid4().hex[:16]}"
            super().save(*args, **kwargs)
            annee = timezone.localtime(self.date_mise_en_place).year
            self.code = f"GR-{annee}-{self.pk:04d}"
            super().save(update_fields=["code"])
            return
        super().save(*args, **kwargs)

    @property
    def age_jours(self):
        date_mise_en_place = timezone.localtime(
            self.date_mise_en_place
        ).date()
        return max(0, (timezone.localdate() - date_mise_en_place).days)

    @property
    def date_entree(self):
        return timezone.localtime(self.date_mise_en_place).date()

    def calendrier_pesees(self, date_derniere_pesee=None):
        """Calendrier indicatif (J30, J60, puis tous les 15 jours) : jamais bloquant."""
        return etat_calendrier(
            self.date_entree,
            timezone.localdate(),
            date_derniere_pesee,
        )

    @property
    def etape_courante(self):
        return self.calendrier_pesees()["etape_courante"]

    @property
    def prochaine_echeance_pesee(self):
        return self.calendrier_pesees()["prochaine_echeance"]

    def __str__(self):
        return self.code or f"Lot de grossissement {self.pk}"

class Pesee(models.Model):
    """Pesée sur échantillon d'un lot de grossissement, à une date.

    Seule la dernière pesée valide d'un lot actif est corrigeable ; une pesée
    n'est jamais supprimée : elle est annulée avec un motif.
    """

    class Statut(models.TextChoices):
        VALIDEE = "VALIDEE", "Validée"
        ANNULEE = "ANNULEE", "Annulée"

    lot_grossissement = models.ForeignKey(
        LotGrossissement,
        on_delete=models.PROTECT,
        related_name="pesees",
    )
    # Bassin du lot au moment de la pesée, copié pour la trace et le filtrage.
    bassin = models.ForeignKey(
        Bassin,
        on_delete=models.PROTECT,
        related_name="pesees",
    )
    date = models.DateField()
    # Pourcentage cible : il fixe la taille d'échantillon attendue.
    pourcentage_echantillon = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=10,
    )
    # Part réellement pesée de l'effectif vivant (calculée).
    pourcentage_reel = models.DecimalField(max_digits=5, decimal_places=2)
    # Effectif vivant lu dans le registre lors de la saisie, copié pour trace.
    # Les indicateurs relisent le registre à chaque recalcul.
    effectif_vivant = models.PositiveIntegerField()
    taille_echantillon_cible = models.PositiveIntegerField()
    nombre_poissons_peses = models.PositiveIntegerField()
    poids_total_g = models.DecimalField(max_digits=12, decimal_places=2)
    poids_moyen = models.DecimalField(max_digits=10, decimal_places=2)
    observation = models.TextField(blank=True, default="")
    statut = models.CharField(
        max_length=10,
        choices=Statut.choices,
        default=Statut.VALIDEE,
    )
    motif_annulation = models.TextField(blank=True, default="")
    annulee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pesees_annulees",
        null=True,
        blank=True,
    )
    annulee_le = models.DateTimeField(null=True, blank=True)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pesees_saisies",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(
                fields=["lot_grossissement", "statut", "date"],
                name="pesee_lot_statut_date_idx",
            ),
            models.Index(fields=["bassin", "date"], name="pesee_bassin_date_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["lot_grossissement", "date"],
                condition=models.Q(statut="VALIDEE"),
                name="pesee_lot_date_unique",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(pourcentage_echantillon__gt=0)
                    & models.Q(pourcentage_echantillon__lte=100)
                ),
                name="pesee_lot_pourcentage_valide",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(pourcentage_reel__gte=0)
                    & models.Q(pourcentage_reel__lte=100)
                ),
                name="pesee_pourcentage_reel_valide",
            ),
            models.CheckConstraint(
                condition=models.Q(effectif_vivant__gt=0),
                name="pesee_lot_effectif_positif",
            ),
            models.CheckConstraint(
                condition=models.Q(taille_echantillon_cible__gte=1),
                name="pesee_lot_cible_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(nombre_poissons_peses__gt=0)
                    & models.Q(nombre_poissons_peses__lte=models.F("effectif_vivant"))
                ),
                name="pesee_lot_nombre_valide",
            ),
            models.CheckConstraint(
                condition=models.Q(poids_total_g__gt=0),
                name="pesee_lot_poids_total_positif",
            ),
            models.CheckConstraint(
                condition=models.Q(poids_moyen__gt=0),
                name="pesee_lot_poids_moyen_positif",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(statut="ANNULEE")
                    | models.Q(motif_annulation__gt="", annulee_le__isnull=False)
                ),
                name="pesee_annulation_motivee",
            ),
        ]

    @property
    def echantillon_conforme(self):
        return self.nombre_poissons_peses >= self.taille_echantillon_cible

    def __str__(self):
        return f"Pesée {self.lot_grossissement} du {self.date}"


class IndicateurLot(models.Model):
    """Instantané des indicateurs d'un lot à une pesée valide, recalculé par le service.

    Cache dérivé : il est supprimé quand sa pesée est annulée.
    """

    pesee = models.OneToOneField(
        Pesee,
        on_delete=models.CASCADE,
        related_name="indicateur",
    )
    effectif_vivant = models.PositiveIntegerField()
    poids_moyen = models.DecimalField(max_digits=10, decimal_places=2)
    biomasse_kg = models.DecimalField(max_digits=12, decimal_places=3)
    gmq_periode = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    gmq_cumule = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    provende_periode_kg = models.DecimalField(
        max_digits=12, decimal_places=3, default=0
    )
    ic_periode = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    ic_cumule = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    jour_de_cycle = models.PositiveIntegerField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(provende_periode_kg__gte=0),
                name="indicateur_lot_provende_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(biomasse_kg__gte=0),
                name="indicateur_lot_biomasse_positive",
            ),
        ]

    def __str__(self):
        return f"Indicateurs {self.pesee}"
