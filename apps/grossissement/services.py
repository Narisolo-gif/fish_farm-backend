from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from .models import LotGrossissement


@transaction.atomic
def cloturer_lot_grossissement(lot_id, motif):
    try:
        lot_grossissement = LotGrossissement.objects.select_for_update().get(
            pk=lot_id
        )
    except LotGrossissement.DoesNotExist as exc:
        raise NotFound("Lot de grossissement introuvable.") from exc
    if not motif or not motif.strip():
        raise ValidationError({"motif": "Le motif de clôture est obligatoire."})
    if lot_grossissement.statut != LotGrossissement.Statut.ACTIF:
        raise ValidationError({"statut": "Seul un lot actif peut être clôturé."})
    lot_grossissement.statut = LotGrossissement.Statut.CLOTURE
    lot_grossissement.date_cloture = timezone.localdate()
    lot_grossissement.motif_cloture = motif.strip()
    lot_grossissement.save(update_fields=[
        "statut",
        "date_cloture",
        "motif_cloture",
        "updated_at",
    ])
    return lot_grossissement


@transaction.atomic
def modifier_lot_grossissement(lot_id, data):
    try:
        lot_grossissement = LotGrossissement.objects.select_for_update().get(
            pk=lot_id
        )
    except LotGrossissement.DoesNotExist as exc:
        raise NotFound("Lot de grossissement introuvable.") from exc
    if lot_grossissement.statut != LotGrossissement.Statut.ACTIF:
        raise ValidationError({"statut": "Seul un lot actif peut être modifié."})
    if "pourcentage_groupe_pesee" in data:
        pourcentage = data["pourcentage_groupe_pesee"]
        if pourcentage <= 0 or pourcentage > 100:
            raise ValidationError({
                "pourcentage_groupe_pesee": (
                    "Le pourcentage doit être supérieur à 0 et inférieur ou égal à 100."
                )
            })
        lot_grossissement.pourcentage_groupe_pesee = pourcentage
    if "observation" in data:
        lot_grossissement.observation = data["observation"]
    lot_grossissement.save(update_fields=[
        "pourcentage_groupe_pesee",
        "observation",
        "updated_at",
    ])
    return lot_grossissement


def lot_a_historique(lot):
    # TODO : inclure les pesées et les répartitions.
    return False