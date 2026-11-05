from __future__ import annotations

from datetime import datetime, time, timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import ExpressionWrapper, F, IntegerField, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.bassins.models import Bassin, Hapa
from apps.lots.models import Lot
from .calculs import (
    effectif_vivant,
    effectifs_du_bassin,
    effectifs_par_hapa,
    lots_presents_dans_hapa,
)
from .models import (
    BlocageSaisie,
    CategoriePerte,
    CauseMortalite,
    EvenementMortalitePerte,
    MouvementEffectif,
    SortieStock,
    SortieStockLigne,
)

JOURS_SAISIE_RETROACTIVE = None


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
    hapa_id: int | None = None,
):
    # Insérer l'événement à sa date réelle oblige à revalider tout le cumul chronologique.
    candidat = {
        "date_evenement": date_evenement,
        "saisi_le": timezone.now(),
        "id": ignore_id,
        "sens": sens,
        "quantite": quantite,
    }

    queryset = MouvementEffectif.objects.filter(
            bassin_id=bassin_id,
            espece=espece,
        )
    if hapa_id is not None:
        queryset = queryset.filter(hapa_id=hapa_id)
    events = list(
        queryset
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


def _verifier_hapa(bassin, hapa, *, champ="id_hapa"):
    hapa_obligatoire = bassin.categorie in {
        Bassin.Categorie.TRAITEMENT,
        Bassin.Categorie.STOCKS,
    }
    hapa_interdit = bassin.categorie in {
        Bassin.Categorie.REPRODUCTION,
        Bassin.Categorie.GROSSISSEMENT,
    }
    if hapa is None:
        if hapa_obligatoire:
            raise ValidationError({
                champ: "Un hapa est obligatoire pour ce bassin."
            })
        return
    if hapa_interdit:
        raise ValidationError({
            champ: "Un hapa est interdit pour cette catégorie de bassin."
        })
    try:
        hapa = Hapa.objects.select_for_update().get(pk=hapa.pk)
    except Hapa.DoesNotExist as exc:
        raise ValidationError({champ: "Hapa introuvable."}) from exc
    if hapa.bassin_id != bassin.pk:
        raise ValidationError({champ: "Le hapa doit appartenir au bassin indiqué."})
    if not hapa.actif or hapa.statut != Hapa.Statut.ACTIF:
        raise ValidationError({champ: "Le hapa sélectionné est inactif."})


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
    _verifier_hapa(bassin, hapa)
    if hapa is not None and type_mouvement == MouvementEffectif.TypeMouvement.ENTREE:
        effectifs_hapa = effectifs_par_hapa(
            bassin.pk, a_la_date=date_evenement
        )
        autres_especes = [
            espece_existante
            for (hapa_id, espece_existante), effectif in effectifs_hapa.items()
            if hapa_id == hapa.pk and espece_existante != espece and effectif > 0
        ]
        if autres_especes:
            raise ValidationError({
                "espece": "Un hapa ne peut contenir qu’une seule espèce."
            })

    _verifier_disponibilite_cumulee(
        bassin.pk,
        espece,
        date_evenement,
        sens,
        quantite,
        hapa_id=hapa.pk if hapa is not None else None,
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
        hapa_id=mouvement.hapa_id,
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
    hapa=None,
    cause_mortalite=None,
    categorie_perte=None,
    justification: str = "",
):
    if not isinstance(date_evenement, datetime):
        date_evenement = timezone.make_aware(
            datetime.combine(date_evenement, time.min),
            timezone.get_current_timezone(),
        )
    return enregistrer_evenement(
        validated_data={
            "bassin": bassin,
            "hapa": hapa,
            "espece": espece,
            "type_evenement": type_evenement,
            "quantite": quantite,
            "date_evenement": date_evenement,
            "cause_mortalite": cause_mortalite,
            "categorie_perte": categorie_perte,
            "justification": justification,
        },
        utilisateur=saisi_par,
    )


@transaction.atomic
def annuler_evenement_mortalite_perte(
    evenement_id: int,
    *,
    motif_annulation: str,
    saisi_par,
):
    evenement = annuler_evenement(
        evenement_id,
        motif=motif_annulation,
        utilisateur=saisi_par,
    )
    mouvement_annulation = MouvementEffectif.objects.get(
        annule_mouvement_id=evenement.mouvement_id,
    )
    return evenement, mouvement_annulation


@transaction.atomic
def bloquer_hapa(hapa, operation, motif):
    hapa = Hapa.objects.select_for_update().get(pk=hapa.pk)
    operation_type = operation._meta.label_lower
    blocage = BlocageSaisie.objects.select_for_update().filter(
        hapa=hapa,
        operation_type=operation_type,
        operation_id=operation.pk,
        actif=True,
    ).first()
    if blocage is not None:
        return blocage
    return BlocageSaisie.objects.create(
        hapa=hapa,
        operation_type=operation_type,
        operation_id=operation.pk,
        motif=motif,
    )


@transaction.atomic
def lever_blocages(operation):
    operation_type = operation._meta.label_lower
    blocages = BlocageSaisie.objects.select_for_update().filter(
        operation_type=operation_type,
        operation_id=operation.pk,
        actif=True,
    )
    return blocages.update(actif=False, leve_le=timezone.now())


def hapa_est_bloque(hapa_id):
    return BlocageSaisie.objects.filter(hapa_id=hapa_id, actif=True).exists()


@transaction.atomic
def enregistrer_evenement(validated_data, utilisateur):
    donnees = dict(validated_data)
    bassin = Bassin.objects.select_for_update().get(
        pk=donnees["bassin"].pk
    )
    if bassin.statut != Bassin.Statut.ACTIF:
        raise ValidationError({
            "id_bassin": "Le bassin sélectionné est inactif."
        })

    hapa = donnees.get("hapa")
    _verifier_hapa(bassin, hapa, champ="id_hapa")
    if hapa is not None and hapa_est_bloque(hapa.pk):
        raise ValidationError({
            "id_hapa": (
                "Hapa bloqué : le stade du lot est clôturé. "
                "Les écarts se justifient à l'ouverture du stade suivant."
            )
        })

    date_evenement = donnees.get("date_evenement") or timezone.now()
    if timezone.is_naive(date_evenement):
        date_evenement = timezone.make_aware(
            date_evenement,
            timezone.get_current_timezone(),
        )
    maintenant = timezone.now()
    if date_evenement > maintenant:
        raise ValidationError({
            "date_evenement": "La date d’événement ne peut pas être dans le futur."
        })
    if (
        JOURS_SAISIE_RETROACTIVE is not None
        and date_evenement < maintenant - timedelta(days=JOURS_SAISIE_RETROACTIVE)
    ):
        raise ValidationError({
            "date_evenement": (
                "La date dépasse la période de saisie rétroactive autorisée."
            )
        })

    quantite = donnees.get("quantite")
    if quantite is None or quantite < 1:
        raise ValidationError({
            "quantite": "La quantité doit être supérieure ou égale à 1."
        })

    type_evenement = donnees.get("type_evenement")
    cause = donnees.get("cause_mortalite")
    categorie = donnees.get("categorie_perte")
    justification = donnees.get("justification", "")
    if type_evenement == EvenementMortalitePerte.TypeEvenement.MORTALITE:
        if cause is None or not CauseMortalite.objects.filter(
            pk=cause.pk,
            actif=True,
        ).exists():
            raise ValidationError({
                "id_cause": "Une cause de mortalité active est obligatoire."
            })
        if categorie is not None:
            raise ValidationError({
                "id_categorie": (
                    "Une mortalité ne peut pas avoir de catégorie de perte."
                )
            })
    elif type_evenement == EvenementMortalitePerte.TypeEvenement.PERTE:
        if categorie is None or not CategoriePerte.objects.filter(
            pk=categorie.pk,
            actif=True,
        ).exists():
            raise ValidationError({
                "id_categorie": "Une catégorie de perte active est obligatoire."
            })
        if cause is not None:
            raise ValidationError({
                "id_cause": "Une perte ne peut pas avoir de cause de mortalité."
            })
        if not justification.strip():
            raise ValidationError({
                "justification": "La justification est obligatoire pour une perte."
            })
    else:
        raise ValidationError({
            "type_evenement": "Type d’événement invalide."
        })

    date_locale = timezone.localtime(date_evenement).date()
    espece = donnees.get("espece")
    if espece is None:
        balances = (
            effectifs_par_hapa(bassin.pk, a_la_date=date_locale)
            if hapa is not None
            else effectifs_du_bassin(bassin.pk, a_la_date=date_locale)
        )
        especes_presentes = {
            espece_ligne
            for (hapa_id, espece_ligne), effectif in balances.items()
            if hapa is not None and hapa_id == hapa.pk and effectif > 0
        } if hapa is not None else {
            espece_ligne
            for espece_ligne, effectif in balances.items()
            if effectif > 0
        }
        if len(especes_presentes) != 1:
            raise ValidationError({
                "espece": "Effectif indéterminé : précisez l'espèce."
            })
        espece = next(iter(especes_presentes))

    if espece not in dict(Lot.Espece.choices):
        raise ValidationError({"espece": "Espèce inconnue."})

    effectif_disponible = effectif_vivant(
        bassin.pk,
        espece,
        a_la_date=date_locale,
        hapa_id=hapa.pk if hapa is not None else None,
    )
    if quantite > effectif_disponible:
        raise ValidationError({
            "quantite": (
                f"La quantité ({quantite}) dépasse l’effectif disponible "
                f"({effectif_disponible}) à cette date."
            )
        })

    lot_origine = None
    if hapa is not None:
        lots = lots_presents_dans_hapa(hapa.pk, a_la_date=date_locale)
        if len(lots) == 1:
            lot_origine = Lot.objects.get(pk=next(iter(lots)))

    evenement = EvenementMortalitePerte.objects.create(
        bassin=bassin,
        hapa=hapa,
        espece=espece,
        type_evenement=type_evenement,
        quantite=quantite,
        date_evenement=date_evenement,
        cause_mortalite=cause,
        categorie_perte=categorie,
        justification=justification.strip() if justification else "",
        observation=donnees.get("observation", ""),
        utilisateur=utilisateur,
    )
    mouvement = enregistrer_mouvement_operation(
        operation=evenement,
        bassin=bassin,
        hapa=hapa,
        espece=espece,
        type_mouvement=(
            MouvementEffectif.TypeMouvement.MORTALITE
            if type_evenement == EvenementMortalitePerte.TypeEvenement.MORTALITE
            else MouvementEffectif.TypeMouvement.PERTE
        ),
        quantite=quantite,
        date_evenement=date_locale,
        saisi_par=utilisateur,
        lot_origine=lot_origine,
    )
    EvenementMortalitePerte.objects.filter(pk=evenement.pk).update(
        mouvement=mouvement,
    )
    evenement.mouvement = mouvement
    return evenement


@transaction.atomic
def annuler_evenement(evenement_id, motif, utilisateur):
    try:
        evenement = EvenementMortalitePerte.objects.select_for_update().get(
            pk=evenement_id
        )
    except EvenementMortalitePerte.DoesNotExist as exc:
        raise ValidationError({"detail": "Événement introuvable."}) from exc
    if evenement.statut == EvenementMortalitePerte.Statut.ANNULEE:
        raise ValidationError({"detail": "Cet événement a déjà été annulé."})
    if not motif or not motif.strip():
        raise ValidationError({"motif": "Le motif d’annulation est obligatoire."})
    if evenement.mouvement_id is None:
        raise ValidationError({
            "detail": "Aucun mouvement n’est associé à cet événement."
        })

    annuler_mouvement(
        evenement.mouvement_id,
        motif_annulation=motif,
        saisi_par=utilisateur,
    )
    annulee_le = timezone.now()
    EvenementMortalitePerte.objects.filter(pk=evenement.pk).update(
        statut=EvenementMortalitePerte.Statut.ANNULEE,
        motif_annulation=motif.strip(),
        annulee_par=utilisateur,
        annulee_le=annulee_le,
    )
    evenement.statut = EvenementMortalitePerte.Statut.ANNULEE
    evenement.motif_annulation = motif.strip()
    evenement.annulee_par = utilisateur
    evenement.annulee_le = annulee_le
    return evenement


def totaux_evenements(filtres=None):
    filtres = filtres or {}
    queryset = EvenementMortalitePerte.objects.filter(
        statut=EvenementMortalitePerte.Statut.VALIDE,
    )
    champs_filtres = {
        "bassin": "bassin_id",
        "hapa": "hapa_id",
        "espece": "espece",
        "type_evenement": "type_evenement",
        "cause": "cause_mortalite_id",
        "categorie": "categorie_perte_id",
    }
    for parametre, champ in champs_filtres.items():
        if filtres.get(parametre) not in (None, ""):
            queryset = queryset.filter(**{champ: filtres[parametre]})
    if filtres.get("date_min"):
        queryset = queryset.filter(
            date_evenement__date__gte=filtres["date_min"]
        )
    if filtres.get("date_max"):
        queryset = queryset.filter(
            date_evenement__date__lte=filtres["date_max"]
        )

    return {
        "par_type": list(
            queryset.values("type_evenement")
            .annotate(total=Sum("quantite"))
            .order_by("type_evenement")
        ),
        "par_cause": list(
            queryset.filter(cause_mortalite__isnull=False)
            .values("cause_mortalite_id", "cause_mortalite__libelle")
            .annotate(total=Sum("quantite"))
            .order_by("cause_mortalite__libelle")
        ),
        "par_categorie": list(
            queryset.filter(categorie_perte__isnull=False)
            .values("categorie_perte_id", "categorie_perte__libelle")
            .annotate(total=Sum("quantite"))
            .order_by("categorie_perte__libelle")
        ),
    }


def _normaliser_date_sortie(date_sortie):
    date_sortie = date_sortie or timezone.now()
    if timezone.is_naive(date_sortie):
        date_sortie = timezone.make_aware(
            date_sortie,
            timezone.get_current_timezone(),
        )
    if date_sortie > timezone.now():
        raise ValidationError({"date_sortie": "La date de sortie ne peut pas être future."})
    return date_sortie


def _verifier_finalite_grossissement(lot):
    mise_en_stock = getattr(lot, "mise_en_stock", None)
    finalite = getattr(mise_en_stock, "finalite", None)
    if finalite != "GROSSISSEMENT":
        if finalite:
            message = f"finalité du lot : {finalite}"
        else:
            message = "finalité du lot non définie"
        raise ValidationError({"id_lot": message})


def _verifier_bassin_destination(bassin):
    if bassin.statut != Bassin.Statut.ACTIF:
        raise ValidationError({"id_bassin_destination": "Le bassin destination doit être actif."})
    if bassin.categorie != Bassin.Categorie.GROSSISSEMENT:
        raise ValidationError({
            "id_bassin_destination": (
                "Le bassin destination doit être de catégorie GROSSISSEMENT."
            )
        })


def calculer_lignes_sortie(lot, date_locale):
    _verifier_finalite_grossissement(lot)
    contributions = (
        MouvementEffectif.objects
        .filter(
            bassin__categorie=Bassin.Categorie.STOCKS,
            lot_origine__isnull=False,
            hapa__isnull=False,
            date_evenement__lte=date_locale,
        )
        .annotate(
            contribution=ExpressionWrapper(
                F("sens") * F("quantite"),
                output_field=IntegerField(),
            )
        )
        .values("bassin_id", "hapa_id", "lot_origine_id")
        .annotate(restant=Sum("contribution"))
    )
    soldes_lots = [
        row for row in contributions
        if row["restant"] and row["restant"] > 0
    ]
    hapas_lot = [
        row for row in soldes_lots
        if row["lot_origine_id"] == lot.pk
    ]
    if not hapas_lot:
        raise ValidationError({"id_lot": "Le lot n’est présent dans aucun hapa de stock."})

    hapa_ids = {row["hapa_id"] for row in hapas_lot}
    bassins_source = {row["bassin_id"] for row in hapas_lot}
    if len(bassins_source) != 1:
        raise ValidationError({
            "id_lot": "Le lot est réparti dans plusieurs bassins de stock."
        })
    hapas = {
        hapa.pk: hapa
        for hapa in Hapa.objects.filter(pk__in=hapa_ids)
        .select_related("bassin")
        .order_by("pk")
    }

    autres_lots = {
        row["lot_origine_id"]
        for row in soldes_lots
        if row["hapa_id"] in hapa_ids and row["lot_origine_id"] != lot.pk
    }
    references_lots = dict(
        Lot.objects.filter(pk__in=autres_lots).values_list("pk", "reference")
    )
    for row in hapas_lot:
        hapa = hapas[row["hapa_id"]]
        if not hapa.actif or hapa.statut != Hapa.Statut.ACTIF:
            raise ValidationError({
                "id_lot": f"Le hapa {hapa.nom} contenant le lot est inactif."
            })
        lot_partage = next(
            (
                item["lot_origine_id"]
                for item in soldes_lots
                if item["hapa_id"] == hapa.pk
                and item["lot_origine_id"] != lot.pk
            ),
            None,
        )
        if lot_partage is not None:
            raise ValidationError({
                "id_lot": (
                    f"Le lot partage le hapa {hapa.nom} avec le lot "
                    f"{references_lots[lot_partage]}."
                )
            })

    effets = (
        MouvementEffectif.objects
        .filter(
            bassin_id=next(iter(bassins_source)),
            hapa_id__in=hapa_ids,
            date_evenement__lte=date_locale,
        )
        .annotate(
            contribution=ExpressionWrapper(
                F("sens") * F("quantite"),
                output_field=IntegerField(),
            )
        )
        .values("hapa_id")
        .annotate(effectif=Sum("contribution"))
    )
    effectifs = {row["hapa_id"]: row["effectif"] or 0 for row in effets}
    effectifs_attribues = {}
    for row in soldes_lots:
        if row["hapa_id"] in hapa_ids:
            effectifs_attribues[row["hapa_id"]] = (
                effectifs_attribues.get(row["hapa_id"], 0) + row["restant"]
            )
    lignes = []
    for row in sorted(hapas_lot, key=lambda item: item["hapa_id"]):
        hapa = hapas[row["hapa_id"]]
        quantite = effectifs.get(hapa.pk, 0)
        if quantite <= 0:
            raise ValidationError({
                "id_lot": f"Le hapa {hapa.nom} n’a plus d’effectif vivant."
            })
        if effectifs_attribues.get(hapa.pk, 0) != quantite:
            raise ValidationError({
                "id_lot": (
                    f"Le contenu du hapa {hapa.nom} ne permet pas de confirmer "
                    "que le lot y est seul."
                )
            })
        lignes.append({"hapa": hapa, "quantite": quantite})
    return lignes


def _verifier_presence_lot(lot, date_sortie):
    return calculer_lignes_sortie(
        lot,
        timezone.localtime(date_sortie).date(),
    )


@transaction.atomic
def creer_sortie(validated_data, utilisateur):
    lot = Lot.objects.select_for_update().get(pk=validated_data["lot"].pk)
    bassin_destination = Bassin.objects.select_for_update().get(
        pk=validated_data["bassin_destination"].pk
    )
    _verifier_bassin_destination(bassin_destination)
    date_sortie = _normaliser_date_sortie(validated_data.get("date_sortie"))
    _verifier_presence_lot(lot, date_sortie)
    try:
        with transaction.atomic():
            return SortieStock.objects.create(
                type_sortie=SortieStock.TypeSortie.GROSSISSEMENT,
                lot=lot,
                bassin_destination=bassin_destination,
                espece=lot.espece,
                date_sortie=date_sortie,
                observation=validated_data.get("observation", ""),
                utilisateur=utilisateur,
            )
    except IntegrityError as exc:
        cause = exc.__cause__
        constraint_name = getattr(
            getattr(cause, "diag", None),
            "constraint_name",
            None,
        )
        if (
            constraint_name == "sortie_stock_lot_active_unique"
            or "UNIQUE constraint failed: effectifs_sortiestock.lot_id"
            in str(cause)
        ):
            raise ValidationError({
                "id_lot": "Une sortie active existe déjà pour ce lot."
            }) from exc
        raise


@transaction.atomic
def modifier_sortie(sortie_id, validated_data):
    sortie = SortieStock.objects.select_for_update().get(pk=sortie_id)
    if sortie.statut != SortieStock.Statut.BROUILLON:
        raise ValidationError({"statut": "Seul un brouillon peut être modifié."})
    lot = Lot.objects.select_for_update().get(pk=sortie.lot_id)
    bassin_destination = Bassin.objects.select_for_update().get(
        pk=validated_data.get(
            "bassin_destination",
            sortie.bassin_destination,
        ).pk
    )
    _verifier_bassin_destination(bassin_destination)
    date_sortie = _normaliser_date_sortie(
        validated_data.get("date_sortie", sortie.date_sortie)
    )
    _verifier_presence_lot(lot, date_sortie)
    sortie.bassin_destination = bassin_destination
    sortie.date_sortie = date_sortie
    if "observation" in validated_data:
        sortie.observation = validated_data["observation"]
    sortie.save()
    return sortie


def _apres_validation_grossissement(sortie):
    from .signals import sortie_stock_validee

    sortie_stock_validee.send(
        sender=SortieStock,
        sortie=sortie,
        utilisateur=sortie.validee_par,
    )


def _avant_annulation_grossissement(sortie, utilisateur):
    from .signals import sortie_stock_avant_annulation

    sortie_stock_avant_annulation.send(
        sender=SortieStock,
        sortie=sortie,
        utilisateur=utilisateur,
    )


@transaction.atomic
def valider_sortie(
    sortie_id,
    effectif_entrant=None,
    justification_ecart="",
    *,
    utilisateur,
):
    sortie = SortieStock.objects.select_for_update().get(pk=sortie_id)
    if sortie.statut != SortieStock.Statut.BROUILLON:
        raise ValidationError({"statut": "Seul un brouillon peut être validé."})
    lot = Lot.objects.select_for_update().get(pk=sortie.lot_id)
    date_sortie = _normaliser_date_sortie(sortie.date_sortie)
    lignes_calculees = calculer_lignes_sortie(
        lot,
        timezone.localtime(date_sortie).date(),
    )
    bassin_source_id = lignes_calculees[0]["hapa"].bassin_id
    bassin_ids = sorted({bassin_source_id, sortie.bassin_destination_id})
    bassins = {
        bassin.pk: bassin
        for bassin in Bassin.objects.select_for_update()
        .filter(pk__in=bassin_ids)
        .order_by("pk")
    }
    if len(bassins) != len(bassin_ids):
        raise ValidationError({"id_bassin_destination": "Un bassin est introuvable."})
    bassin_source = bassins[bassin_source_id]
    bassin_destination = bassins[sortie.bassin_destination_id]
    if bassin_source.categorie != Bassin.Categorie.STOCKS:
        raise ValidationError({"id_lot": "Le lot n’est pas dans un bassin de stock."})
    _verifier_bassin_destination(bassin_destination)
    hapa_ids = sorted(ligne["hapa"].pk for ligne in lignes_calculees)
    hapas_verrouilles = list(
        Hapa.objects.select_for_update()
        .filter(pk__in=hapa_ids)
        .order_by("pk")
    )
    if len(hapas_verrouilles) != len(hapa_ids):
        raise ValidationError({"id_lot": "Un hapa du lot est introuvable."})
    date_locale = timezone.localtime(date_sortie).date()
    lignes_calculees = calculer_lignes_sortie(lot, date_locale)
    effectif_sortant = sum(ligne["quantite"] for ligne in lignes_calculees)
    if effectif_sortant <= 0:
        raise ValidationError({"id_lot": "L’effectif sortant doit être positif."})
    if effectif_entrant is None:
        effectif_entrant = effectif_sortant
    if effectif_entrant <= 0:
        raise ValidationError({"effectif_entrant": "L’effectif entrant doit être positif."})
    if effectif_entrant > effectif_sortant:
        raise ValidationError({
            "effectif_entrant": "L’effectif entrant ne peut pas dépasser l’effectif sortant."
        })
    if effectif_entrant != effectif_sortant and not justification_ecart.strip():
        raise ValidationError({
            "justification_ecart": "Une justification est obligatoire en cas d’écart."
        })

    SortieStockLigne.objects.bulk_create([
        SortieStockLigne(
            sortie=sortie,
            hapa=ligne["hapa"],
            quantite=ligne["quantite"],
        )
        for ligne in lignes_calculees
    ])
    for ligne in lignes_calculees:
        enregistrer_mouvement_operation(
            operation=sortie,
            bassin=bassin_source,
            espece=lot.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
            quantite=ligne["quantite"],
            date_evenement=date_locale,
            saisi_par=utilisateur,
            lot_origine=lot,
            hapa=ligne["hapa"],
        )
    enregistrer_mouvement_operation(
        operation=sortie,
        bassin=bassin_destination,
        espece=lot.espece,
        type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
        quantite=effectif_entrant,
        date_evenement=date_locale,
        saisi_par=utilisateur,
        lot_origine=lot,
    )

    sortie.date_sortie = date_sortie
    sortie.effectif_sortant = effectif_sortant
    sortie.effectif_entrant = effectif_entrant
    sortie.justification_ecart = justification_ecart.strip()
    sortie.statut = SortieStock.Statut.VALIDEE
    sortie.validee_par = utilisateur
    sortie.validee_le = timezone.now()
    sortie.save()
    _apres_validation_grossissement(sortie)
    return sortie


@transaction.atomic
def annuler_sortie(sortie_id, motif, utilisateur):
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

    if sortie.statut == SortieStock.Statut.VALIDEE:
        _avant_annulation_grossissement(sortie, utilisateur)
        content_type = ContentType.objects.get_for_model(sortie)
        mouvements = MouvementEffectif.objects.filter(
            operation_source_content_type=content_type,
            operation_source_object_id=sortie.pk,
        )
        mouvements_operation = list(
            mouvements.filter(
                type_mouvement__in=[
                    MouvementEffectif.TypeMouvement.ENTREE,
                    MouvementEffectif.TypeMouvement.SORTIE,
                ]
            )
            .select_for_update()
            .select_related("bassin")
            .order_by("id")
        )
        mouvements_destination = [
            mouvement
            for mouvement in mouvements_operation
            if (
                mouvement.bassin_id == sortie.bassin_destination_id
                and mouvement.type_mouvement
                == MouvementEffectif.TypeMouvement.ENTREE
            )
        ]
        mouvements_source = [
            mouvement
            for mouvement in mouvements_operation
            if mouvement.type_mouvement == MouvementEffectif.TypeMouvement.SORTIE
        ]
        if len(mouvements_source) != sortie.lignes.count():
            raise ValidationError({
                "detail": "Les mouvements de sortie source sont incomplets."
            })
        if len(mouvements_destination) != 1:
            raise ValidationError({
                "detail": "Mouvement d’entrée de destination introuvable."
            })
        bassin_ids = sorted({
            sortie.bassin_destination_id,
            *(mouvement.bassin_id for mouvement in mouvements_source),
        })
        list(
            Bassin.objects.select_for_update()
            .filter(pk__in=bassin_ids)
            .order_by("pk")
        )
        for mouvement_destination in reversed(mouvements_destination):
            annuler_mouvement(
                mouvement_destination.pk,
                motif_annulation=motif,
                saisi_par=utilisateur,
            )
        for mouvement_source in reversed(mouvements_source):
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
