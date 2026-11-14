from django.dispatch import receiver
from rest_framework.exceptions import ValidationError

from apps.effectifs.signals import (
    mouvement_avant_annulation,
    sortie_stock_avant_annulation,
)
from apps.lots.models import Lot

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
