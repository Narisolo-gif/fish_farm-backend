from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.bassins.models import Bassin, Hapa
from apps.effectifs.calculs import effectif_vivant
from apps.effectifs.models import MouvementEffectif
from apps.effectifs.services import enregistrer_mouvement_operation
from apps.lots.models import Lot

from .models import LotGrossissement


MAX_LOTS_ACTIFS_PAR_BASSIN = 2


@transaction.atomic
def cloturer_lot_grossissement(
    lot_id,
    *,
    motif,
    poids_final_g,
    utilisateur,
    date_cloture=None,
):
    try:
        lot_grossissement = LotGrossissement.objects.select_for_update().get(
            pk=lot_id
        )
    except LotGrossissement.DoesNotExist as exc:
        raise NotFound("Lot de grossissement introuvable.") from exc
    if lot_grossissement.statut != LotGrossissement.Statut.ACTIF:
        raise ValidationError({"statut": "Seul un lot actif peut être clôturé."})
    if not motif or not motif.strip():
        raise ValidationError({"motif": "Le motif de clôture est obligatoire."})
    if poids_final_g is None or poids_final_g <= 0:
        raise ValidationError({
            "poids_final_g": "Le poids final doit être strictement positif."
        })

    date_cloture = date_cloture or timezone.localdate()
    date_mise_en_place = timezone.localtime(
        lot_grossissement.date_mise_en_place
    ).date()
    if date_cloture > timezone.localdate():
        raise ValidationError({
            "date_cloture": "La date de clôture ne peut pas être dans le futur."
        })
    if date_cloture < date_mise_en_place:
        raise ValidationError({
            "date_cloture": (
                "La date de clôture ne peut pas précéder la mise en place du lot."
            )
        })

    bassin = Bassin.objects.select_for_update().get(pk=lot_grossissement.bassin_id)
    lot_origine = Lot.objects.select_for_update().get(
        pk=lot_grossissement.lot_origine_id
    )
    # Bassin + espèce identifient le lot : au plus un lot actif par espèce et par bassin.
    if MouvementEffectif.objects.filter(
        bassin=bassin,
        espece=lot_grossissement.espece,
        date_evenement__gt=date_cloture,
    ).exists():
        raise ValidationError({
            "date_cloture": "Des mouvements existent après la date de clôture."
        })
    effectif = effectif_vivant(
        bassin.pk,
        lot_grossissement.espece,
        a_la_date=date_cloture,
    )
    if effectif > 0:
        # Évacuation : ramène l'effectif du lot à zéro dans le registre.
        enregistrer_mouvement_operation(
            operation=lot_grossissement,
            bassin=bassin,
            espece=lot_grossissement.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
            quantite=effectif,
            date_evenement=date_cloture,
            saisi_par=utilisateur,
            lot_origine=lot_origine,
            lot_grossissement=lot_origine,
        )

    lot_grossissement.statut = LotGrossissement.Statut.CLOTURE
    lot_grossissement.date_cloture = date_cloture
    lot_grossissement.motif_cloture = motif.strip()
    lot_grossissement.poids_final_g = poids_final_g
    lot_grossissement.effectif_final = effectif
    lot_grossissement.save(update_fields=[
        "statut",
        "date_cloture",
        "motif_cloture",
        "poids_final_g",
        "effectif_final",
        "updated_at",
    ])
    lot_origine.stade = Lot.Stade.CLOTUREE
    lot_origine.save(update_fields=["stade", "updated_at"])
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
    if "poids_initial_g" in data:
        poids = data["poids_initial_g"]
        if poids is None or poids <= 0:
            raise ValidationError({
                "poids_initial_g": "Le poids initial doit être strictement positif."
            })
        lot_grossissement.poids_initial_g = poids
    if "observation" in data:
        lot_grossissement.observation = data["observation"]
    champs = [
        champ
        for champ in ("pourcentage_groupe_pesee", "poids_initial_g", "observation")
        if champ in data
    ]
    lot_grossissement.save(update_fields=[*champs, "updated_at"])
    return lot_grossissement


def lot_a_historique(lot):
    # TODO : inclure les pesées et les répartitions.
    return False

def verifier_capacite_bassin(bassin, espece):
    """Contrôle d'accueil d'un nouveau lot ; le bassin doit être verrouillé par l'appelant."""
    if bassin.hapas.filter(statut=Hapa.Statut.ACTIF).exists():
        raise ValidationError({
            "id_bassin_destination": (
                "Le bassin de grossissement ne doit contenir aucun hapa actif."
            )
        })
    lots_actifs = LotGrossissement.objects.filter(
        bassin=bassin,
        statut=LotGrossissement.Statut.ACTIF,
    )
    if lots_actifs.count() >= MAX_LOTS_ACTIFS_PAR_BASSIN:
        raise ValidationError({
            "id_bassin_destination": (
                "Le bassin contient déjà deux lots de grossissement en cours."
            )
        })
    if lots_actifs.filter(espece=espece).exists():
        raise ValidationError({
            "id_bassin_destination": (
                "Le bassin contient déjà un lot en cours de la même espèce."
            )
        })
