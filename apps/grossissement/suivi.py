"""Suivi de cycle d'un lot de grossissement (lecture seule).

Assemble le contrat de la page de suivi à partir des données déjà calculées :
les instantanés `IndicateurLot` ne sont jamais recalculés ici. Tout est vu à
la date de référence (pesées, effectif, environnement, calendrier, alertes).
Les valeurs sont renvoyées en types Python (Decimal, date) ; la mise au
format JSON est faite par `serializers.SuiviLotSerializer`.
"""
from datetime import datetime, time, timedelta

from django.utils import timezone

from apps.effectifs.calculs import effectif_vivant
from apps.environnement.models import ParametreEnvironnemental

from .calculs import (
    alertes_suivi,
    biomasse_kg,
    etat_calendrier,
    etat_seuil,
    seuil_poids_vente,
)
from .models import IndicateurLot, LotGrossissement, Pesee


NATURE = {
    "poids_moyen": "mesuré sur échantillon",
    "biomasse_kg": "estimée",
}
CHAMPS_INDICATEURS = (
    "gmq_periode",
    "gmq_cumule",
    "ic_periode",
    "ic_cumule",
    "biomasse_kg",
    "provende_periode_kg",
)
CHAMPS_ENVIRONNEMENT = (
    "temperature_eau",
    "temperature_air",
    "oxygene",
    "ph",
    "nitrite",
    "nitrate",
    "ammoniac",
)


def _point_de_serie(pesee, date_entree):
    try:
        indicateur = pesee.indicateur
    except IndicateurLot.DoesNotExist:
        indicateur = None
    point = {
        "pesee_id": pesee.pk,
        "date": pesee.date,
        "jour_de_cycle": (pesee.date - date_entree).days,
        "poids_moyen": pesee.poids_moyen,
        # Effectif relu dans le registre au dernier recalcul (trace sinon).
        "effectif_vivant": (
            indicateur.effectif_vivant if indicateur is not None else pesee.effectif_vivant
        ),
    }
    for champ in CHAMPS_INDICATEURS:
        point[champ] = getattr(indicateur, champ) if indicateur is not None else None
    return point


def _dernieres_valeurs(series):
    if not series:
        return None
    dernier = series[-1]
    return {
        "date_pesee": dernier["date"],
        "jour_de_cycle": dernier["jour_de_cycle"],
        "effectif_vivant": dernier["effectif_vivant"],
        "poids_moyen": dernier["poids_moyen"],
        "biomasse_kg": dernier["biomasse_kg"],
        "gmq_periode": dernier["gmq_periode"],
        "gmq_cumule": dernier["gmq_cumule"],
        "ic_periode": dernier["ic_periode"],
        "ic_cumule": dernier["ic_cumule"],
        "provende_periode_kg": dernier["provende_periode_kg"],
        "nature": dict(NATURE),
    }


def _parametres_environnementaux(bassin_id, date_reference):
    fin_du_jour = timezone.make_aware(
        datetime.combine(date_reference + timedelta(days=1), time.min),
        timezone.get_current_timezone(),
    )
    mesure = (
        ParametreEnvironnemental.objects.filter(
            bassin_id=bassin_id,
            date_heure_prelevement__lt=fin_du_jour,
        )
        .order_by("-date_heure_prelevement", "-id")
        .first()
    )
    if mesure is None:
        return None
    return {
        "date_heure_prelevement": mesure.date_heure_prelevement,
        **{champ: getattr(mesure, champ) for champ in CHAMPS_ENVIRONNEMENT},
    }


def suivi_lot(lot, date_reference):
    """Contrat du suivi de cycle du lot à la date de référence."""
    actif = lot.statut == LotGrossissement.Statut.ACTIF
    date_entree = lot.date_entree
    # Après la clôture, le cycle est figé : jour de cycle et calendrier s'arrêtent.
    date_cycle = (
        min(date_reference, lot.date_cloture)
        if lot.date_cloture is not None
        else date_reference
    )

    pesees = list(
        Pesee.objects.filter(
            lot_grossissement=lot,
            statut=Pesee.Statut.VALIDEE,
            date__lte=date_reference,
        )
        .select_related("indicateur")
        .order_by("date", "id")
    )
    series = [_point_de_serie(pesee, date_entree) for pesee in pesees]

    calendrier = etat_calendrier(
        date_entree,
        date_cycle,
        pesees[-1].date if pesees else None,
    )
    prochaine_pesee = calendrier["prochaine_pesee"] if actif else None
    seuil_g = seuil_poids_vente(lot.espece)
    seuil = etat_seuil(pesees, seuil_g)

    poids_initial = lot.poids_moyen_initial
    return {
        "lot": {
            "id": lot.pk,
            "code": lot.code,
            "espece": lot.espece,
            "id_bassin": lot.bassin_id,
            "reference_bassin": lot.bassin.reference,
            "statut": lot.statut,
            "date_entree": date_entree,
            "jour_de_cycle": max(0, (date_cycle - date_entree).days),
            "effectif_initial": lot.effectif_initial,
            "poids_moyen_initial": poids_initial,
            "effectif_vivant_actuel": effectif_vivant(
                lot.bassin_id, lot.espece, a_la_date=date_reference
            ),
            "evacuation_requise": lot.evacuation_requise,
        },
        "nombre_pesees": len(series),
        "dernieres_valeurs": _dernieres_valeurs(series),
        "point_initial": {
            "date": date_entree,
            "jour_de_cycle": 0,
            "poids_moyen": poids_initial,
            "biomasse_kg": (
                biomasse_kg(lot.effectif_initial, poids_initial)
                if poids_initial is not None
                else None
            ),
        },
        "series": series,
        "calendrier": {
            "prochaine_pesee": prochaine_pesee,
            "pesees_prevues": calendrier["pesees_prevues"],
        },
        "seuil": {
            "poids_vente_g": seuil_g,
            "atteint": seuil["atteint"],
            "fin_cycle_possible": seuil["atteint"] and actif,
            "date_premiere_pesee_au_seuil": seuil["date_premiere_pesee_au_seuil"],
        },
        "alertes": alertes_suivi(
            actif=actif,
            prochaine_pesee=prochaine_pesee,
            seuil=seuil,
            seuil_g=seuil_g,
        ),
        "parametres_environnementaux": _parametres_environnementaux(
            lot.bassin_id, date_reference
        ),
        "autres_lots_du_bassin": [
            {"id": autre.pk, "code": autre.code, "espece": autre.espece}
            for autre in LotGrossissement.objects.filter(
                bassin_id=lot.bassin_id,
                statut=LotGrossissement.Statut.ACTIF,
            )
            .exclude(pk=lot.pk)
            .order_by("id")
        ],
    }
