from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.bassins.models import Bassin
from .calculs import effectifs_du_bassin
from .models import (
    CategoriePerte,
    CauseMortalite,
    EvenementMortalitePerte,
    MouvementEffectif,
    SortieStock,
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


def _normaliser_date_sortie(date_sortie):
    date_sortie = date_sortie or timezone.now()
    if timezone.is_naive(date_sortie):
        date_sortie = timezone.make_aware(
            date_sortie,
            timezone.get_current_timezone(),
        )
    return date_sortie.replace(second=0, microsecond=0)


def _verrouiller_bassins_sortie(bassin_source_id, bassin_destination_id=None):
    # Un ordre stable évite que deux transferts verrouillent les bassins à l'inverse.
    bassin_ids = sorted({
        bassin_id
        for bassin_id in (bassin_source_id, bassin_destination_id)
        if bassin_id is not None
    })
    bassins = {
        bassin.pk: bassin
        for bassin in (
            Bassin.objects.select_for_update()
            .filter(pk__in=bassin_ids)
            .order_by("pk")
        )
    }
    if len(bassins) != len(bassin_ids):
        raise ValidationError({"id_bassin_source": "Un bassin est introuvable."})
    return bassins


def _valider_donnees_sortie(
    *,
    type_sortie,
    bassin_source,
    bassin_destination,
    espece,
    quantite,
    date_sortie,
):
    if type_sortie not in SortieStock.TypeSortie.values:
        raise ValidationError({"type_sortie": "Type de sortie inconnu."})
    if quantite <= 0:
        raise ValidationError({"quantite": "La quantité doit être strictement positive."})

    date_sortie = _normaliser_date_sortie(date_sortie)
    if date_sortie > timezone.now():
        raise ValidationError({"date_sortie": "La date de sortie ne peut pas être future."})

    bassins = _verrouiller_bassins_sortie(
        bassin_source.pk,
        bassin_destination.pk if bassin_destination else None,
    )
    bassin_source = bassins[bassin_source.pk]
    bassin_destination = (
        bassins[bassin_destination.pk]
        if bassin_destination is not None
        else None
    )

    if bassin_source.statut != Bassin.Statut.ACTIF:
        raise ValidationError({
            "id_bassin_source": "Le bassin source doit être actif."
        })
    if bassin_source.categorie != Bassin.Categorie.STOCKS:
        raise ValidationError({
            "id_bassin_source": "Le bassin source doit être de catégorie STOCKS."
        })
    if (
        bassin_destination is not None
        and bassin_destination.pk == bassin_source.pk
    ):
        raise ValidationError({
            "id_bassin_destination": "La source et la destination doivent être distinctes."
        })
    if type_sortie == SortieStock.TypeSortie.GROSSISSEMENT:
        if bassin_destination is None:
            raise ValidationError({
                "id_bassin_destination": "Le bassin destination est obligatoire."
            })
        if bassin_destination.statut != Bassin.Statut.ACTIF:
            raise ValidationError({
                "id_bassin_destination": "Le bassin destination doit être actif."
            })
        if bassin_destination.categorie != Bassin.Categorie.GROSSISSEMENT:
            raise ValidationError({
                "id_bassin_destination": (
                    "Le bassin destination doit être de catégorie GROSSISSEMENT."
                )
            })
    elif bassin_destination is not None:
        raise ValidationError({
            "id_bassin_destination": "Une vente ne peut pas avoir de bassin destination."
        })

    return date_sortie, bassin_source, bassin_destination


@transaction.atomic
def creer_sortie_stock(validated_data, utilisateur):
    date_sortie, bassin_source, bassin_destination = _valider_donnees_sortie(
        type_sortie=validated_data["type_sortie"],
        bassin_source=validated_data["bassin_source"],
        bassin_destination=validated_data.get("bassin_destination"),
        espece=validated_data["espece"],
        quantite=validated_data["quantite"],
        date_sortie=validated_data.get("date_sortie"),
    )
    donnees = dict(validated_data)
    donnees.update({
        "bassin_source": bassin_source,
        "bassin_destination": bassin_destination,
        "date_sortie": date_sortie,
        "utilisateur": utilisateur,
    })
    return SortieStock.objects.create(**donnees)


@transaction.atomic
def modifier_sortie_stock(sortie_id, validated_data):
    sortie = SortieStock.objects.select_for_update().get(pk=sortie_id)
    if sortie.statut != SortieStock.Statut.BROUILLON:
        raise ValidationError({
            "statut": "Seul un brouillon peut être modifié."
        })

    donnees = {
        "type_sortie": validated_data.get("type_sortie", sortie.type_sortie),
        "bassin_source": validated_data.get("bassin_source", sortie.bassin_source),
        "bassin_destination": validated_data.get(
            "bassin_destination",
            sortie.bassin_destination,
        ),
        "espece": validated_data.get("espece", sortie.espece),
        "quantite": validated_data.get("quantite", sortie.quantite),
        "date_sortie": validated_data.get("date_sortie", sortie.date_sortie),
    }
    date_sortie, bassin_source, bassin_destination = _valider_donnees_sortie(
        **donnees,
    )
    for field, value in donnees.items():
        setattr(sortie, field, value)
    sortie.bassin_source = bassin_source
    sortie.bassin_destination = bassin_destination
    sortie.date_sortie = date_sortie
    if "observation" in validated_data:
        sortie.observation = validated_data["observation"]
    sortie.save()
    return sortie


def _apres_validation_grossissement(sortie):
    # Point d'extension réservé à la création du lot de grossissement (étape 6).
    pass


@transaction.atomic
def valider_sortie_stock(sortie_id, utilisateur):
    sortie = SortieStock.objects.select_for_update().get(pk=sortie_id)
    if sortie.statut != SortieStock.Statut.BROUILLON:
        raise ValidationError({"statut": "Seul un brouillon peut être validé."})

    date_sortie, bassin_source, bassin_destination = _valider_donnees_sortie(
        type_sortie=sortie.type_sortie,
        bassin_source=sortie.bassin_source,
        bassin_destination=sortie.bassin_destination,
        espece=sortie.espece,
        quantite=sortie.quantite,
        date_sortie=sortie.date_sortie,
    )
    date_locale = timezone.localtime(date_sortie).date()
    effectif_disponible = effectifs_du_bassin(
        bassin_source.pk,
        a_la_date=date_locale,
    ).get(sortie.espece, 0)
    if effectif_disponible < sortie.quantite:
        raise ValidationError({
            "quantite": (
                f"Effectif disponible : {effectif_disponible}; "
                f"quantité demandée : {sortie.quantite}."
            )
        })

    type_sortie = sortie.type_sortie
    date_evenement = date_locale
    content_type = ContentType.objects.get_for_model(sortie)
    if type_sortie == SortieStock.TypeSortie.GROSSISSEMENT:
        # La transaction englobe les deux mouvements et le changement de statut.
        enregistrer_mouvement(
            bassin=bassin_source,
            espece=sortie.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
            quantite=sortie.quantite,
            date_evenement=date_evenement,
            saisi_par=utilisateur,
            operation_source_content_type=content_type,
            operation_source_object_id=sortie.pk,
        )
        enregistrer_mouvement(
            bassin=bassin_destination,
            espece=sortie.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            quantite=sortie.quantite,
            date_evenement=date_evenement,
            saisi_par=utilisateur,
            operation_source_content_type=content_type,
            operation_source_object_id=sortie.pk,
        )
    else:
        enregistrer_mouvement(
            bassin=bassin_source,
            espece=sortie.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.VENTE,
            quantite=sortie.quantite,
            date_evenement=date_evenement,
            saisi_par=utilisateur,
            operation_source_content_type=content_type,
            operation_source_object_id=sortie.pk,
        )

    sortie.date_sortie = date_sortie
    sortie.statut = SortieStock.Statut.VALIDEE
    sortie.validee_par = utilisateur
    sortie.validee_le = timezone.now()
    sortie.save()
    if type_sortie == SortieStock.TypeSortie.GROSSISSEMENT:
        _apres_validation_grossissement(sortie)
    return sortie


@transaction.atomic
def annuler_sortie_stock(sortie_id, motif, utilisateur):
    sortie = SortieStock.objects.select_for_update().get(pk=sortie_id)
    if sortie.statut not in {
        SortieStock.Statut.BROUILLON,
        SortieStock.Statut.VALIDEE,
    }:
        raise ValidationError({
            "statut": "Seul un brouillon ou une sortie validée peut être annulé."
        })
    if not motif or not motif.strip():
        raise ValidationError({"motif": "Le motif d’annulation est obligatoire."})

    _verrouiller_bassins_sortie(
        sortie.bassin_source_id,
        sortie.bassin_destination_id,
    )
    if sortie.statut == SortieStock.Statut.VALIDEE:
        content_type = ContentType.objects.get_for_model(sortie)
        mouvements = MouvementEffectif.objects.filter(
            operation_source_content_type=content_type,
            operation_source_object_id=sortie.pk,
        )
        if sortie.type_sortie == SortieStock.TypeSortie.GROSSISSEMENT:
            # Annuler d'abord l'entrée permet de refuser si les poissons ont quitté la destination.
            try:
                mouvement_destination = mouvements.get(
                    bassin_id=sortie.bassin_destination_id,
                    type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
                )
            except MouvementEffectif.DoesNotExist as exc:
                raise ValidationError({
                    "detail": "Mouvement d’entrée de destination introuvable."
                }) from exc
            annuler_mouvement(
                mouvement_destination.pk,
                motif_annulation=motif,
                saisi_par=utilisateur,
            )

        type_mouvement_source = (
            MouvementEffectif.TypeMouvement.VENTE
            if sortie.type_sortie == SortieStock.TypeSortie.VENTE
            else MouvementEffectif.TypeMouvement.SORTIE
        )
        try:
            mouvement_source = mouvements.get(
                bassin_id=sortie.bassin_source_id,
                type_mouvement=type_mouvement_source,
            )
        except MouvementEffectif.DoesNotExist as exc:
            raise ValidationError({
                "detail": "Mouvement de sortie source introuvable."
            }) from exc
        annuler_mouvement(
            mouvement_source.pk,
            motif_annulation=motif,
            saisi_par=utilisateur,
        )

    sortie.statut = SortieStock.Statut.ANNULEE
    sortie.motif_annulation = motif.strip()
    sortie.annulee_par = utilisateur
    sortie.annulee_le = timezone.now()
    sortie.save()
    return sortie
