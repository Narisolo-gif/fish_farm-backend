from django.contrib.contenttypes.models import ContentType
from django.dispatch import receiver
from rest_framework.exceptions import ValidationError

from django.utils import timezone

from apps.bassins.models import Bassin
from apps.effectifs.models import MouvementEffectif
from apps.effectifs.signals import (
    mouvement_avant_annulation,
    mouvement_avant_enregistrement,
    sortie_stock_avant_annulation,
)
from apps.lots.models import Lot
from apps.provende.signals import (
    distribution_avant_annulation,
    distribution_avant_enregistrement,
)

from .models import LotGrossissement
from .services import lot_a_historique


# La création du lot de grossissement ne passe plus par un signal : elle est
# faite par `services.valider_sortie_grossissement`, dans la même transaction
# que la sortie de stock.


@receiver(
    sortie_stock_avant_annulation,
    dispatch_uid="grossissement.supprimer_lot_avant_annulation",
)
def supprimer_lot_avant_annulation(sender, *, sortie, utilisateur, **kwargs):
    lot_grossissement = (
        LotGrossissement.objects.select_for_update()
        .select_related("lot_origine")
        .filter(sortie_stock=sortie)
        .first()
    )
    if lot_grossissement is None:
        return
    lot = Lot.objects.select_for_update().get(pk=lot_grossissement.lot_origine_id)
    if lot_grossissement.statut != LotGrossissement.Statut.ACTIF:
        raise ValidationError({
            "statut": "Le lot de grossissement doit être actif pour annuler la sortie."
        })
    # Le poids initial est saisi à la validation : seul un historique (pesées)
    # empêche d'annuler la sortie.
    if lot_a_historique(lot_grossissement):
        raise ValidationError({
            "detail": (
                "Le lot de grossissement possède un historique de pesées : "
                "la sortie ne peut plus être annulée."
            )
        })
    lot_grossissement.delete()
    lot.stade = Lot.Stade.EN_STOCK
    lot.save(update_fields=["stade", "updated_at"])


@receiver(
    mouvement_avant_annulation,
    dispatch_uid="grossissement.figer_mouvements_lot_cloture",
)
def refuser_annulation_lot_cloture(sender, *, mouvement, date_annulation, **kwargs):
    # Bassin + espèce identifient le lot : après la clôture, le registre de la
    # période du lot est figé, sinon des poissons « reviendraient » sans lot.
    for date_reference in {mouvement.date_evenement, date_annulation}:
        if LotGrossissement.objects.filter(
            bassin_id=mouvement.bassin_id,
            espece=mouvement.espece,
            statut=LotGrossissement.Statut.CLOTURE,
            date_mise_en_place__date__lte=date_reference,
            date_cloture__gte=date_reference,
        ).exists():
            raise ValidationError({
                "detail": (
                    "Ce mouvement appartient à un lot de grossissement clôturé : "
                    "il ne peut plus être annulé."
                )
            })


@receiver(
    mouvement_avant_enregistrement,
    dispatch_uid="grossissement.figer_registre_lot_cloture",
)
def refuser_mouvement_lot_cloture(
    sender,
    *,
    bassin,
    espece,
    type_mouvement,
    date_evenement,
    operation_source_content_type,
    operation_source_object_id,
    **kwargs,
):
    """Un lot clôturé ne reçoit plus d'enregistrement.

    Bassin + espèce identifient le lot. Sont refusés : tout mouvement daté de
    la période du lot (mise en place → clôture), et tout mouvement postérieur
    tant que le bassin n'est pas évacué. Seule l'évacuation du lot passe ; après
    elle, un nouveau lot peut entrer dès le jour de la clôture.
    """
    lots_clotures = LotGrossissement.objects.filter(
        bassin=bassin,
        espece=espece,
        statut=LotGrossissement.Statut.CLOTURE,
    )
    if not lots_clotures.exists():
        return
    type_lot = ContentType.objects.get_for_model(LotGrossissement)
    for lot in lots_clotures:
        if (
            operation_source_content_type == type_lot
            and operation_source_object_id == lot.pk
        ):
            continue
        dans_la_periode = lot.date_entree <= date_evenement <= lot.date_cloture
        entree_apres_evacuation = (
            date_evenement == lot.date_cloture
            and type_mouvement == MouvementEffectif.TypeMouvement.ENTREE
            and not lot.evacuation_requise
        )
        if dans_la_periode and not entree_apres_evacuation:
            raise ValidationError({
                "detail": (
                    f"Le lot de grossissement {lot.code} est clôturé : son registre "
                    "est figé, aucun mouvement ne peut y être ajouté."
                )
            })
        if date_evenement > lot.date_cloture and lot.evacuation_requise:
            raise ValidationError({
                "detail": (
                    f"Le lot de grossissement {lot.code} est clôturé mais le bassin "
                    "n'a pas été évacué : évacuez-le avant toute nouvelle saisie."
                )
            })


# --- Provende : chiffres biologiques d'un lot clôturé figés -------------------

def _refuser_periode_lot_cloture(bassin, jour, action):
    """Refuse une écriture de provende datée de la période d'un lot clôturé.

    La provende d'un jour est répartie entre les lots présents ce jour-là :
    toucher à la période d'un lot clôturé changerait ses indicateurs (ICA).
    """
    lot = (
        LotGrossissement.objects.filter(
            bassin=bassin,
            statut=LotGrossissement.Statut.CLOTURE,
            date_mise_en_place__date__lte=jour,
            date_cloture__gte=jour,
        )
        .order_by("-date_cloture")
        .first()
    )
    if lot is not None:
        raise ValidationError({
            "date_distribution": (
                f"Le lot de grossissement {lot.code} est clôturé : ses chiffres sont "
                f"figés, aucune distribution ne peut être {action} sur sa période "
                f"(jusqu'au {lot.date_cloture.isoformat()})."
            )
        })


@receiver(
    distribution_avant_enregistrement,
    dispatch_uid="grossissement.provende_lot_en_cours",
)
def refuser_distribution_sans_lot_en_cours(sender, *, bassin, date_locale, **kwargs):
    """En grossissement, la provende n'est saisie que pour un lot en cours."""
    if bassin.categorie != Bassin.Categorie.GROSSISSEMENT:
        return
    _refuser_periode_lot_cloture(bassin, date_locale, "enregistrée")
    if not LotGrossissement.objects.filter(
        bassin=bassin,
        statut=LotGrossissement.Statut.ACTIF,
        date_mise_en_place__date__lte=date_locale,
    ).exists():
        raise ValidationError({
            "id_bassin": (
                "Aucun lot de grossissement en cours dans ce bassin à cette date : "
                "la provende ne peut pas être enregistrée."
            )
        })


@receiver(
    distribution_avant_annulation,
    dispatch_uid="grossissement.provende_annulation_lot_cloture",
)
def refuser_annulation_distribution_lot_cloture(sender, *, distribution, **kwargs):
    bassin = distribution.bassin
    if bassin.categorie != Bassin.Categorie.GROSSISSEMENT:
        return
    jour = timezone.localtime(distribution.date_distribution).date()
    _refuser_periode_lot_cloture(bassin, jour, "annulée")
