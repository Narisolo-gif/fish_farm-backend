from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.bassins.models import Bassin

from .models import (
    CategoriePerte,
    CauseMortalite,
    EvenementMortalitePerte,
    MouvementEffectif,
)


def _sens_pour_type(type_mouvement: str) -> int:
    # Le signe est une règle métier : il ne doit jamais être choisi par l'appelant.
    if type_mouvement in {
        MouvementEffectif.TypeMouvement.ENTREE,
        MouvementEffectif.TypeMouvement.AJUSTEMENT_POSITIF,
    }:
        return MouvementEffectif.Sens.POSITIF

    if type_mouvement in {
        MouvementEffectif.TypeMouvement.SORTIE,
        MouvementEffectif.TypeMouvement.MORTALITE,
        MouvementEffectif.TypeMouvement.PERTE,
        MouvementEffectif.TypeMouvement.VENTE,
    }:
        return MouvementEffectif.Sens.NEGATIF

    raise ValidationError({
        "type_mouvement": "Ce type de mouvement ne peut pas être créé directement."
    })


def _verifier_disponibilite_cumulee(
    bassin_id: int,
    espece: str,
    date_evenement,
    sens: int,
    quantite: int,
    *,
    ignore_id: int | None = None,
):
    # Insérer l'événement à sa date réelle oblige à revalider tout le cumul chronologique.
    candidat = {
        "date_evenement": date_evenement,
        "saisi_le": timezone.now(),
        "id": ignore_id,
        "sens": sens,
        "quantite": quantite,
    }

    events = list(
        MouvementEffectif.objects.filter(
            bassin_id=bassin_id,
            espece=espece,
        )
        .exclude(id=ignore_id)
        .order_by("date_evenement", "saisi_le", "id")
        .values("date_evenement", "saisi_le", "id", "sens", "quantite")
    )
    candidat["id"] = (
        ignore_id
        if ignore_id is not None
        else max((event["id"] for event in events), default=0) + 1
    )
    events.append(candidat)
    events.sort(key=lambda e: (e["date_evenement"], e["saisi_le"], e["id"]))

    # Une saisie rétroactive est refusée si elle rend négatif un solde ultérieur.
    cumul = 0
    for event in events:
        cumul += int(event["sens"]) * int(event["quantite"])
        if cumul < 0:
            raise ValidationError({
                "quantite": (
                    "Le cumul d’effectif du bassin pour cette espèce devient négatif "
                    "à la date donnée."
                )
            })


@transaction.atomic
def enregistrer_mouvement(
    *,
    bassin,
    espece: str,
    type_mouvement: str,
    quantite: int,
    date_evenement,
    saisi_par,
    lot_origine=None,
    hapa=None,
    lot_grossissement=None,
    operation_source_content_type=None,
    operation_source_object_id=None,
):
    """Crée un mouvement d’effectif et valide le solde cumulatif."""
    if type_mouvement == MouvementEffectif.TypeMouvement.ANNULATION:
        raise ValidationError({
            "type_mouvement": "Le type ANNULATION doit passer par l’annulation d’un mouvement existant."
        })

    if date_evenement > timezone.localdate():
        raise ValidationError({
            "date_evenement": "La date d’événement ne peut pas être dans le futur."
        })

    # Le verrou du bassin sérialise les écritures concurrentes avant le contrôle du cumul.
    bassin = Bassin.objects.select_for_update().get(pk=bassin.pk)
    if bassin.statut != Bassin.Statut.ACTIF:
        raise ValidationError({
            "bassin": "Le bassin sélectionné est inactif."
        })

    if quantite <= 0:
        raise ValidationError({
            "quantite": "La quantité doit être strictement positive."
        })

    sens = _sens_pour_type(type_mouvement)

    _verifier_disponibilite_cumulee(
        bassin.pk,
        espece,
        date_evenement,
        sens,
        quantite,
    )

    mouvement = MouvementEffectif.objects.create(
        bassin=bassin,
        espece=espece,
        type_mouvement=type_mouvement,
        sens=sens,
        quantite=quantite,
        date_evenement=date_evenement,
        lot_origine=lot_origine,
        hapa=hapa,
        lot_grossissement=lot_grossissement,
        operation_source_content_type=operation_source_content_type,
        operation_source_object_id=operation_source_object_id,
        saisi_par=saisi_par,
    )
    return mouvement


def operation_a_mouvements(operation) -> bool:
    content_type = ContentType.objects.get_for_model(operation)
    return MouvementEffectif.objects.filter(
        operation_source_content_type=content_type,
        operation_source_object_id=operation.pk,
    ).exists()


def enregistrer_mouvement_operation(
    *,
    operation,
    **mouvement_data,
):
    """Enregistre un mouvement référencé par son opération métier."""
    # Le lien générique conserve la traçabilité sans imposer un modèle d'opération unique.
    content_type = ContentType.objects.get_for_model(operation)
    return enregistrer_mouvement(
        **mouvement_data,
        operation_source_content_type=content_type,
        operation_source_object_id=operation.pk,
    )


@transaction.atomic
def annuler_mouvement(mouvement_id: int, *, motif_annulation: str, saisi_par, date_annulation=None):
    """Crée un contre-mouvement de type ANNULATION et protège l’immuabilité."""
    if not motif_annulation or not motif_annulation.strip():
        raise ValidationError({
            "motif_annulation": "Le motif d’annulation est obligatoire."
        })

    # Le verrou sur le mouvement empêche deux annulations concurrentes de passer ensemble.
    mouvement = (
        MouvementEffectif.objects
        # Les relations facultatives jointes plus bas ne doivent pas être verrouillées.
        .select_for_update(of=("self",))
        .select_related("bassin", "lot_origine", "hapa", "lot_grossissement", "saisi_par")
        .get(pk=mouvement_id)
    )

    if mouvement.type_mouvement == MouvementEffectif.TypeMouvement.ANNULATION:
        raise ValidationError({
            "detail": "Un mouvement d’annulation ne peut pas être annulé à nouveau."
        })

    if mouvement.annulations_associees.exists():
        raise ValidationError({
            "detail": "Ce mouvement a déjà été annulé."
        })

    if mouvement.annule_mouvement_id is not None:
        raise ValidationError({
            "detail": "Ce mouvement est lui-même une annulation."
        })

    if date_annulation is None:
        date_annulation = mouvement.date_evenement

    if date_annulation > timezone.localdate():
        raise ValidationError({
            "date_annulation": "La date d’annulation ne peut pas être dans le futur."
        })

    # Utilise le même verrou que les créations afin de protéger le solde du bassin.
    Bassin.objects.select_for_update().get(pk=mouvement.bassin_id)

    sens_annulation = -mouvement.sens
    _verifier_disponibilite_cumulee(
        mouvement.bassin_id,
        mouvement.espece,
        date_annulation,
        sens_annulation,
        mouvement.quantite,
    )

    annulation = MouvementEffectif.objects.create(
        bassin=mouvement.bassin,
        espece=mouvement.espece,
        type_mouvement=MouvementEffectif.TypeMouvement.ANNULATION,
        sens=sens_annulation,
        quantite=mouvement.quantite,
        date_evenement=date_annulation,
        lot_origine=mouvement.lot_origine,
        hapa=mouvement.hapa,
        lot_grossissement=mouvement.lot_grossissement,
        operation_source_content_type=mouvement.operation_source_content_type,
        operation_source_object_id=mouvement.operation_source_object_id,
        annule_mouvement=mouvement,
        motif_annulation=motif_annulation.strip(),
        saisi_par=saisi_par,
    )
    return annulation


def solde_bassin_espece_date(bassin_id: int, espece: str, date_reference):
    """Retourne le solde cumulé pour le bassin et l’espèce à une date donnée."""
    # Les annulations sont déjà des mouvements inverses ; elles s'intègrent au cumul.
    mouvements = MouvementEffectif.objects.filter(
        bassin_id=bassin_id,
        espece=espece,
        date_evenement__lte=date_reference,
    ).order_by("date_evenement", "saisi_le", "id")

    cumul = 0
    for mouvement in mouvements:
        cumul += int(mouvement.sens) * int(mouvement.quantite)
    return cumul


@transaction.atomic
def enregistrer_evenement_mortalite_perte(
    *,
    bassin,
    espece: str,
    type_evenement: str,
    quantite: int,
    date_evenement,
    saisi_par,
    cause_mortalite=None,
    categorie_perte=None,
    justification: str = "",
):
    if type_evenement not in EvenementMortalitePerte.TypeEvenement.values:
        raise ValidationError({"type_evenement": "Type d’événement invalide."})
    if quantite <= 0:
        raise ValidationError({"quantite": "La quantité doit être positive."})
    if date_evenement > timezone.localdate():
        raise ValidationError({
            "date_evenement": "La date d’événement ne peut pas être dans le futur."
        })

    # Le verrou partagé avec les mouvements garantit la disponibilité lors de saisies concurrentes.
    bassin = Bassin.objects.select_for_update().get(pk=bassin.pk)
    if bassin.statut != Bassin.Statut.ACTIF:
        raise ValidationError({"bassin": "Le bassin sélectionné est inactif."})

    if type_evenement == EvenementMortalitePerte.TypeEvenement.MORTALITE:
        cause_is_active = (
            cause_mortalite is not None
            and CauseMortalite.objects.filter(
                pk=cause_mortalite.pk,
                actif=True,
            ).exists()
        )
        if not cause_is_active:
            raise ValidationError({
                "cause_mortalite": "Une cause de mortalité active est obligatoire."
            })
        if categorie_perte is not None:
            raise ValidationError({
                "categorie_perte": "Une mortalité ne peut pas avoir de catégorie de perte."
            })
    else:
        category_is_active = (
            categorie_perte is not None
            and CategoriePerte.objects.filter(
                pk=categorie_perte.pk,
                actif=True,
            ).exists()
        )
        if not category_is_active:
            raise ValidationError({
                "categorie_perte": "Une catégorie de perte active est obligatoire."
            })
        if cause_mortalite is not None:
            raise ValidationError({
                "cause_mortalite": "Une perte ne peut pas avoir de cause de mortalité."
            })
        if not justification or not justification.strip():
            raise ValidationError({
                "justification": "La justification est obligatoire pour une perte."
            })

    effectif_disponible = solde_bassin_espece_date(
        bassin.pk,
        espece,
        date_evenement,
    )
    if quantite > effectif_disponible:
        raise ValidationError({
            "quantite": (
                f"La quantité ({quantite}) dépasse l’effectif disponible "
                f"({effectif_disponible}) à cette date."
            )
        })

    evenement = EvenementMortalitePerte.objects.create(
        bassin=bassin,
        espece=espece,
        type_evenement=type_evenement,
        quantite=quantite,
        date_evenement=date_evenement,
        cause_mortalite=cause_mortalite,
        categorie_perte=categorie_perte,
        justification=justification.strip() if justification else "",
        saisi_par=saisi_par,
    )
    mouvement = enregistrer_mouvement_operation(
        operation=evenement,
        bassin=bassin,
        espece=espece,
        type_mouvement=(
            MouvementEffectif.TypeMouvement.MORTALITE
            if type_evenement == EvenementMortalitePerte.TypeEvenement.MORTALITE
            else MouvementEffectif.TypeMouvement.PERTE
        ),
        quantite=quantite,
        date_evenement=date_evenement,
        saisi_par=saisi_par,
    )
    EvenementMortalitePerte.objects.filter(pk=evenement.pk).update(
        mouvement=mouvement,
    )
    evenement.mouvement = mouvement
    return evenement


@transaction.atomic
def annuler_evenement_mortalite_perte(
    evenement_id: int,
    *,
    motif_annulation: str,
    saisi_par,
):
    evenement = (
        EvenementMortalitePerte.objects
        .select_for_update()
        .get(pk=evenement_id)
    )
    if evenement.statut == EvenementMortalitePerte.Statut.ANNULE:
        raise ValidationError({"detail": "Cet événement a déjà été annulé."})
    if evenement.mouvement_id is None:
        raise ValidationError({
            "detail": "Aucun mouvement n’est associé à cet événement."
        })

    # L'annulation conserve l'événement et compense son mouvement dans la même transaction.
    mouvement_annulation = annuler_mouvement(
        evenement.mouvement_id,
        motif_annulation=motif_annulation,
        saisi_par=saisi_par,
    )
    EvenementMortalitePerte.objects.filter(pk=evenement.pk).update(
        statut=EvenementMortalitePerte.Statut.ANNULE,
    )
    evenement.statut = EvenementMortalitePerte.Statut.ANNULE
    return evenement, mouvement_annulation
