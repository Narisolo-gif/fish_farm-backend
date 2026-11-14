from django.dispatch import receiver
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.effectifs.signals import (
    mouvement_avant_annulation,
    sortie_stock_avant_annulation,
    sortie_stock_validee,
)
from apps.bassins.models import Bassin
from apps.lots.models import Lot

from .models import LotGrossissement
from .services import lot_a_historique, verifier_capacite_bassin


@receiver(
    sortie_stock_validee,
    dispatch_uid="grossissement.creer_lot_apres_sortie_validee",
)
def creer_lot_apres_sortie_validee(sender, *, sortie, utilisateur, **kwargs):
    lot = Lot.objects.select_for_update().get(pk=sortie.lot_id)
    if lot.stade != Lot.Stade.EN_STOCK:
        raise ValidationError({
            "id_lot": "Le lot d'origine doit être au stade EN_STOCK."
        })
    bassin = Bassin.objects.select_for_update().get(pk=sortie.bassin_destination_id)
    verifier_capacite_bassin(
        bassin,
        sortie.espece,
        timezone.localtime(sortie.date_sortie).date(),
    )
    LotGrossissement.objects.create(
        lot_origine=lot,
        sortie_stock=sortie,
        bassin=bassin,
        espece=sortie.espece,
        date_mise_en_place=sortie.date_sortie,
        effectif_initial=sortie.effectif_entrant,
        utilisateur=sortie.validee_par or utilisateur,
    )
    lot.stade = Lot.Stade.EN_GROSSISSEMENT
    lot.save(update_fields=["stade", "updated_at"])


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
    if lot_grossissement.poids_initial_g is not None or lot_a_historique(
        lot_grossissement
    ):
        raise ValidationError({
            "detail": (
                "Le lot de grossissement possède un poids initial ou un historique."
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
