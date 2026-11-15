from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import ExpressionWrapper, F, IntegerField, Max, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.bassins.models import Bassin, Hapa
from apps.effectifs.calculs import effectif_vivant
from apps.effectifs.models import MouvementEffectif
from apps.effectifs import services as services_effectifs
from apps.effectifs.services import (
    enregistrer_mouvement_operation,
    enregistrer_sortie_stock_validee,
    preparer_sortie_stock,
    verifier_ecart_sortie,
)
from apps.lots.models import Lot
from apps.provende.models import ConsommationProvende

from .calculs import (
    LotCalcul,
    PeseeCalcul,
    allouer_provende,
    calculer_instantanes,
    ecart_poids_pourcentage,
    echantillon_conforme,
    poids_moyen_echantillon,
    pourcentage_reel_echantillon,
    seuil_poids_vente,
    taille_echantillon_cible,
)
from .models import IndicateurLot, LotGrossissement, Pesee


MAX_LOTS_ACTIFS_PAR_BASSIN = 2


def preparer_cloture(lot):
    """Résumé de la clôture du lot à la date du jour ; rien n'est écrit.

    La clôture n'est possible que pour un lot actif dont la dernière pesée
    valide atteint le seuil de vente de son espèce. Le poids moyen final est
    celui de cette pesée ; l'effectif final est l'effectif vivant du registre.
    """
    date_cloture = timezone.localdate()
    derniere = derniere_pesee_valide(lot)
    seuil = seuil_poids_vente(lot.espece)
    poids_final = derniere.poids_moyen if derniere is not None else None

    refus = None
    if lot.statut != LotGrossissement.Statut.ACTIF:
        refus = ("statut", "Seul un lot en cours de grossissement peut être clôturé.")
    elif derniere is None:
        refus = (
            "poids_moyen_final",
            "Aucune pesée enregistrée : effectuez une pesée finale avant de clôturer.",
        )
    elif poids_final < seuil:
        refus = (
            "poids_moyen_final",
            f"Le poids moyen de la dernière pesée ({poids_final} g, le "
            f"{derniere.date.isoformat()}) est inférieur au seuil de vente de "
            f"{seuil} g ({lot.espece}). Enregistrez une pesée finale à jour.",
        )
    return {
        "lot": lot,
        "date_cloture": date_cloture,
        "effectif_initial": lot.effectif_initial,
        "effectif_final": effectif_vivant(lot.bassin_id, lot.espece, a_la_date=date_cloture),
        "poids_moyen_initial": lot.poids_moyen_initial,
        "poids_moyen_final": poids_final,
        "date_derniere_pesee": derniere.date if derniere is not None else None,
        "seuil_poids_vente_g": seuil,
        "ecart_poids_pourcentage": ecart_poids_pourcentage(
            lot.poids_moyen_initial, poids_final
        ),
        "cloturable": refus is None,
        "champ_refus": refus[0] if refus else None,
        "raison_refus": refus[1] if refus else None,
    }


def _verrouiller_lot_et_bassin(lot_id):
    """Verrouille le bassin puis le lot (même ordre que les pesées)."""
    bassin_id = (
        LotGrossissement.objects.filter(pk=lot_id)
        .values_list("bassin_id", flat=True)
        .first()
    )
    if bassin_id is None:
        raise NotFound("Lot de grossissement introuvable.")
    bassin = Bassin.objects.select_for_update().get(pk=bassin_id)
    lot = LotGrossissement.objects.select_for_update().get(pk=lot_id)
    return lot, bassin


@transaction.atomic
def cloturer_lot_grossissement(lot_id, *, utilisateur, observation=""):
    """Clôture le cycle (une transaction) ; l'évacuation est une opération à part.

    Statut CLOTURE, date du jour, effectif et poids moyen finaux, lot d'origine
    CLOTUREE. Aucun mouvement n'est écrit : le registre du lot est figé jusqu'à
    l'évacuation (voir `evacuer_lot_grossissement`).
    """
    lot_grossissement, _ = _verrouiller_lot_et_bassin(lot_id)
    lot_origine = Lot.objects.select_for_update().get(pk=lot_grossissement.lot_origine_id)
    contexte = preparer_cloture(lot_grossissement)
    if not contexte["cloturable"]:
        raise ValidationError({contexte["champ_refus"]: contexte["raison_refus"]})

    lot_grossissement.statut = LotGrossissement.Statut.CLOTURE
    lot_grossissement.date_cloture = contexte["date_cloture"]
    lot_grossissement.effectif_final = contexte["effectif_final"]
    lot_grossissement.poids_moyen_final = contexte["poids_moyen_final"]
    lot_grossissement.motif_cloture = (observation or "").strip()
    lot_grossissement.save(update_fields=[
        "statut",
        "date_cloture",
        "effectif_final",
        "poids_moyen_final",
        "motif_cloture",
        "updated_at",
    ])
    lot_origine.stade = Lot.Stade.CLOTUREE
    lot_origine.save(update_fields=["stade", "updated_at"])
    return lot_grossissement


@transaction.atomic
def evacuer_lot_grossissement(lot_id, *, utilisateur):
    """Évacue le bassin d'un lot clôturé : SORTIE de l'effectif final.

    Le mouvement est daté du jour de la clôture : le registre du lot étant figé
    depuis, l'effectif à sortir est exactement l'effectif final.
    """
    lot_grossissement, bassin = _verrouiller_lot_et_bassin(lot_id)
    if lot_grossissement.statut != LotGrossissement.Statut.CLOTURE:
        raise ValidationError({"statut": "Seul un lot clôturé peut être évacué."})
    if lot_grossissement.evacue_le is not None:
        raise ValidationError({"detail": "Le bassin a déjà été évacué pour ce lot."})
    effectif = effectif_vivant(
        bassin.pk,
        lot_grossissement.espece,
        a_la_date=lot_grossissement.date_cloture,
    )
    if effectif <= 0:
        raise ValidationError({"detail": "Aucun poisson du lot à évacuer."})
    lot_origine = Lot.objects.get(pk=lot_grossissement.lot_origine_id)
    enregistrer_mouvement_operation(
        operation=lot_grossissement,
        bassin=bassin,
        espece=lot_grossissement.espece,
        type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
        quantite=effectif,
        date_evenement=lot_grossissement.date_cloture,
        saisi_par=utilisateur,
        lot_origine=lot_origine,
        lot_grossissement=lot_origine,
    )
    lot_grossissement.evacue_le = timezone.now()
    lot_grossissement.evacue_par = utilisateur
    lot_grossissement.save(update_fields=["evacue_le", "evacue_par", "updated_at"])
    return lot_grossissement


@transaction.atomic
def modifier_lot_grossissement(lot_id, data):
    bassin_id = (
        LotGrossissement.objects.filter(pk=lot_id)
        .values_list("bassin_id", flat=True)
        .first()
    )
    if bassin_id is None:
        raise NotFound("Lot de grossissement introuvable.")
    # Même ordre de verrous que les pesées : bassin, puis lot.
    Bassin.objects.select_for_update().get(pk=bassin_id)
    lot_grossissement = LotGrossissement.objects.select_for_update().get(pk=lot_id)
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
    # Le poids moyen initial n'est plus modifiable : il vient de la pesée
    # initiale (P0) saisie à la validation de la sortie.
    champs = [
        champ
        for champ in ("pourcentage_groupe_pesee", "observation")
        if champ in data
    ]
    lot_grossissement.save(update_fields=[*champs, "updated_at"])
    return lot_grossissement


def lot_a_historique(lot):
    # Toute pesée, même annulée, est un historique : elle n'est jamais
    # supprimée et protège le lot (PROTECT).
    # TODO : inclure les répartitions de provende.
    return lot.pesees.exists()

def verifier_capacite_bassin(bassin, espece, date_mise_en_place=None):
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
    # Les poissons d'un lot clôturé non évacué sont encore dans le registre.
    if LotGrossissement.objects.filter(
        bassin=bassin,
        espece=espece,
        statut=LotGrossissement.Statut.CLOTURE,
        evacue_le__isnull=True,
        effectif_final__gt=0,
    ).exists():
        raise ValidationError({
            "id_bassin_destination": (
                "Un lot clôturé de cette espèce n'a pas encore été évacué du bassin."
            )
        })
    # Bassin + espèce identifient le lot : un nouveau lot ne peut pas s'intercaler
    # avant des mouvements d'un lot précédent de même espèce (clôture comprise).
    if date_mise_en_place is not None and MouvementEffectif.objects.filter(
        bassin=bassin,
        espece=espece,
        date_evenement__gt=date_mise_en_place,
    ).exists():
        raise ValidationError({
            "date_mise_en_place": (
                "Des mouvements de cette espèce existent dans le bassin après "
                "cette date : la mise en place ne peut pas être antérieure."
            )
        })



# --- Pesée sur échantillon -------------------------------------------------

MESSAGE_ECHANTILLON_NON_CONFORME = (
    "Le nombre de poissons pesés est inférieur à la taille d'échantillon cible "
    "pour au moins un lot. Confirmez la saisie avec "
    "confirmer_echantillon_non_conforme."
)


class EchantillonNonConforme(Exception):
    """Échantillon inférieur à la cible sans confirmation : la pesée est refusée."""

    def __init__(self, lignes_non_conformes):
        super().__init__(MESSAGE_ECHANTILLON_NON_CONFORME)
        self.message = MESSAGE_ECHANTILLON_NON_CONFORME
        self.lignes_non_conformes = lignes_non_conformes


# Mêmes règles pour la pesée initiale (P0) et les pesées de suivi.
_calculer_cible = taille_echantillon_cible
_calculer_poids_moyen = poids_moyen_echantillon


def _date_entree(lot):
    return timezone.localtime(lot.date_mise_en_place).date()


def derniere_pesee_valide(lot):
    """Dernière pesée valide du lot, ou None."""
    return (
        Pesee.objects.filter(lot_grossissement=lot, statut=Pesee.Statut.VALIDEE)
        .order_by("-date", "-id")
        .first()
    )


def _verifier_lot_pesable(lot, bassin):
    if lot.statut != LotGrossissement.Statut.ACTIF:
        raise ValidationError({
            "id_lot_grossissement": "Seul un lot de grossissement actif peut être pesé."
        })
    if bassin.statut != Bassin.Statut.ACTIF:
        raise ValidationError({
            "id_lot_grossissement": "Le bassin du lot doit être actif."
        })
    if bassin.categorie != Bassin.Categorie.GROSSISSEMENT:
        raise ValidationError({
            "id_lot_grossissement": (
                "Le bassin du lot doit être de catégorie GROSSISSEMENT."
            )
        })


def _verifier_date_pesee(lot, date_pesee):
    """Date libre dans le cycle : au plus aujourd'hui, après la dernière pesée."""
    if date_pesee > timezone.localdate():
        raise ValidationError({
            "date": "La date de pesée ne peut pas être dans le futur."
        })
    if date_pesee < _date_entree(lot):
        raise ValidationError({
            "date": (
                "La date de pesée ne peut pas précéder la mise en place "
                f"du lot ({_date_entree(lot)})."
            )
        })
    derniere = derniere_pesee_valide(lot)
    if derniere is not None and date_pesee <= derniere.date:
        raise ValidationError({
            "date": (
                "La date doit être postérieure à la dernière pesée du lot "
                f"({derniere.date})."
            )
        })


def _calculer_mesures(lot, date_pesee, *, pourcentage, nombre, poids_total, complet=True):
    """Contrôle les mesures et renvoie les valeurs calculées de la pesée.

    À l'aperçu (`complet=False`), le nombre et le poids peuvent manquer :
    les valeurs qui en dépendent valent alors None.
    """
    if pourcentage is None:
        pourcentage = lot.pourcentage_groupe_pesee
    if pourcentage <= 0 or pourcentage > 100:
        raise ValidationError({
            "pourcentage_echantillon": (
                "Le pourcentage doit être supérieur à 0 et inférieur ou égal à 100."
            )
        })
    if (complet or nombre is not None) and (nombre is None or nombre <= 0):
        raise ValidationError({
            "nombre_poissons_peses": (
                "Le nombre de poissons pesés doit être strictement positif."
            )
        })
    if (complet or poids_total is not None) and (poids_total is None or poids_total <= 0):
        raise ValidationError({
            "poids_total_g": "Le poids total doit être strictement positif."
        })
    effectif = effectif_vivant(lot.bassin_id, lot.espece, a_la_date=date_pesee)
    if effectif <= 0:
        raise ValidationError({
            "effectif_vivant": (
                "L'effectif vivant est nul à la date de la pesée, "
                "aucune pesée n'est possible."
            )
        })
    if nombre is not None and nombre > effectif:
        raise ValidationError({
            "nombre_poissons_peses": (
                f"Le nombre de poissons pesés ({nombre}) dépasse "
                f"l'effectif vivant ({effectif})."
            )
        })
    poids_moyen = (
        _calculer_poids_moyen(poids_total, nombre)
        if nombre is not None and poids_total is not None
        else None
    )
    if poids_moyen is not None and poids_moyen <= 0:
        raise ValidationError({
            "poids_total_g": "Le poids moyen calculé est nul, vérifiez le poids total."
        })
    return {
        "pourcentage_echantillon": pourcentage,
        "effectif_vivant": effectif,
        "taille_echantillon_cible": _calculer_cible(effectif, pourcentage),
        "nombre_poissons_peses": nombre,
        "poids_total_g": poids_total,
        "poids_moyen": poids_moyen,
        "pourcentage_reel": (
            pourcentage_reel_echantillon(nombre, effectif)
            if nombre is not None
            else None
        ),
    }


def _verifier_conformite(lot, valeurs, confirmer):
    if (
        valeurs["nombre_poissons_peses"] < valeurs["taille_echantillon_cible"]
        and not confirmer
    ):
        raise EchantillonNonConforme([{
            "id_lot_grossissement": lot.pk,
            "reference_lot": lot.code,
            "nombre_poissons_peses": valeurs["nombre_poissons_peses"],
            "taille_echantillon_cible": valeurs["taille_echantillon_cible"],
        }])


def _verrouiller_lot(lot_id):
    """Verrouille le bassin puis le lot (même ordre que les autres services)."""
    bassin_id = (
        LotGrossissement.objects.filter(pk=lot_id)
        .values_list("bassin_id", flat=True)
        .first()
    )
    if bassin_id is None:
        raise ValidationError({
            "id_lot_grossissement": "Lot de grossissement introuvable."
        })
    bassin = Bassin.objects.select_for_update().get(pk=bassin_id)
    lot = LotGrossissement.objects.select_for_update().get(pk=lot_id)
    return lot, bassin


def apercu_pesee(donnees):
    """Valeurs préremplies et calculées du formulaire de pesée ; rien n'est écrit."""
    lot = (
        LotGrossissement.objects.select_related("bassin")
        .filter(pk=donnees["id_lot_grossissement"])
        .first()
    )
    if lot is None:
        raise ValidationError({
            "id_lot_grossissement": "Lot de grossissement introuvable."
        })
    _verifier_lot_pesable(lot, lot.bassin)
    date_pesee = donnees.get("date") or timezone.localdate()
    _verifier_date_pesee(lot, date_pesee)
    valeurs = _calculer_mesures(
        lot,
        date_pesee,
        pourcentage=donnees.get("pourcentage_echantillon"),
        nombre=donnees.get("nombre_poissons_peses"),
        poids_total=donnees.get("poids_total_g"),
        complet=False,
    )
    derniere = derniere_pesee_valide(lot)
    return {
        "lot": lot,
        "date": date_pesee,
        "date_derniere_pesee": derniere.date if derniere is not None else None,
        "date_min": (
            derniere.date + timedelta(days=1)
            if derniere is not None
            else _date_entree(lot)
        ),
        "echantillon_conforme": (
            valeurs["nombre_poissons_peses"] >= valeurs["taille_echantillon_cible"]
            if valeurs["nombre_poissons_peses"] is not None
            else None
        ),
        **valeurs,
    }


@transaction.atomic
def creer_pesee(
    *,
    lot_id,
    date_pesee,
    nombre,
    poids_total,
    utilisateur,
    pourcentage=None,
    observation="",
    confirmer=False,
):
    lot, bassin = _verrouiller_lot(lot_id)
    _verifier_lot_pesable(lot, bassin)
    _verifier_date_pesee(lot, date_pesee)
    valeurs = _calculer_mesures(
        lot,
        date_pesee,
        pourcentage=pourcentage,
        nombre=nombre,
        poids_total=poids_total,
    )
    _verifier_conformite(lot, valeurs, confirmer)
    pesee = Pesee.objects.create(
        lot_grossissement=lot,
        bassin=bassin,
        date=date_pesee,
        utilisateur=utilisateur,
        observation=observation or "",
        **valeurs,
    )
    recalculer_indicateurs_bassin(bassin.pk)
    return pesee


def _verrouiller_pesee(pesee_id):
    lot_id = (
        Pesee.objects.filter(pk=pesee_id)
        .values_list("lot_grossissement_id", flat=True)
        .first()
    )
    if lot_id is None:
        raise NotFound("Pesée introuvable.")
    lot, _ = _verrouiller_lot(lot_id)
    pesee = Pesee.objects.select_for_update().get(pk=pesee_id)
    pesee.lot_grossissement = lot
    return pesee, lot


def _verifier_pesee_modifiable(pesee, lot):
    """Seule la dernière pesée valide d'un lot actif est corrigeable ou annulable."""
    if pesee.statut != Pesee.Statut.VALIDEE:
        raise ValidationError({
            "detail": "Cette pesée est annulée : elle ne peut plus être modifiée."
        })
    if lot.statut != LotGrossissement.Statut.ACTIF:
        raise ValidationError({
            "detail": (
                "Le lot n'est plus actif : la pesée ne peut plus être "
                "modifiée ni annulée."
            )
        })
    if Pesee.objects.filter(
        lot_grossissement=lot,
        statut=Pesee.Statut.VALIDEE,
        date__gt=pesee.date,
    ).exists():
        raise ValidationError({
            "detail": (
                "Une pesée plus récente existe : seule la dernière pesée "
                "du lot peut être modifiée ou annulée."
            )
        })


@transaction.atomic
def modifier_pesee(pesee_id, donnees, *, confirmer=False):
    """Corrige les mesures de la dernière pesée ; la date reste figée."""
    pesee, lot = _verrouiller_pesee(pesee_id)
    _verifier_pesee_modifiable(pesee, lot)
    valeurs = _calculer_mesures(
        lot,
        pesee.date,
        pourcentage=donnees.get(
            "pourcentage_echantillon", pesee.pourcentage_echantillon
        ),
        nombre=donnees.get("nombre_poissons_peses", pesee.nombre_poissons_peses),
        poids_total=donnees.get("poids_total_g", pesee.poids_total_g),
    )
    _verifier_conformite(lot, valeurs, confirmer)
    for champ, valeur in valeurs.items():
        setattr(pesee, champ, valeur)
    champs = list(valeurs)
    if "observation" in donnees:
        pesee.observation = donnees["observation"] or ""
        champs.append("observation")
    pesee.save(update_fields=[*champs, "updated_at"])
    recalculer_indicateurs_bassin(pesee.bassin_id)
    return pesee


@transaction.atomic
def annuler_pesee(pesee_id, *, motif, utilisateur):
    """Annule la dernière pesée valide ; la précédente redevient corrigeable."""
    motif = (motif or "").strip()
    if not motif:
        raise ValidationError({"motif": "Le motif d'annulation est obligatoire."})
    pesee, lot = _verrouiller_pesee(pesee_id)
    _verifier_pesee_modifiable(pesee, lot)
    pesee.statut = Pesee.Statut.ANNULEE
    pesee.motif_annulation = motif
    pesee.annulee_par = utilisateur
    pesee.annulee_le = timezone.now()
    pesee.save(update_fields=[
        "statut",
        "motif_annulation",
        "annulee_par",
        "annulee_le",
        "updated_at",
    ])
    recalculer_indicateurs_bassin(pesee.bassin_id)
    return pesee


# --- Indicateurs -------------------------------------------------------------

def _debut_jour(jour):
    return timezone.make_aware(
        datetime.combine(jour, time.min),
        timezone.get_current_timezone(),
    )


def _effectifs_a_la_date(bassin_id, lots, date_max):
    """Effectif vivant par lot et par jour, calculé en mémoire.

    Même règle que `effectifs.calculs.effectif_vivant` : Σ(sens × quantité)
    des mouvements du bassin et de l'espèce datés au plus tard du jour. Une
    seule requête pour toute la période.
    """
    especes = {lot.pk: lot.espece for lot in lots}
    variations = (
        MouvementEffectif.objects.filter(
            bassin_id=bassin_id,
            espece__in=set(especes.values()),
            date_evenement__lte=date_max,
        )
        .annotate(
            contribution=ExpressionWrapper(
                F("sens") * F("quantite"),
                output_field=IntegerField(),
            )
        )
        .values("espece", "date_evenement")
        .annotate(variation=Sum("contribution"))
        .order_by("espece", "date_evenement")
    )
    dates = defaultdict(list)
    cumuls = defaultdict(list)
    for ligne in variations:
        espece = ligne["espece"]
        precedent = cumuls[espece][-1] if cumuls[espece] else 0
        dates[espece].append(ligne["date_evenement"])
        cumuls[espece].append(precedent + (ligne["variation"] or 0))

    def effectif_a(lot_id, jour):
        espece = especes[lot_id]
        rang = bisect_right(dates[espece], jour)
        return cumuls[espece][rang - 1] if rang else 0

    return effectif_a


def recalculer_indicateurs_bassin(bassin_id):
    """Recalcule les instantanés de tous les lots pesés du bassin (idempotent).

    Tout le bassin est recalculé : la part de provende d'un lot dépend des
    poids et des effectifs des autres lots présents. Seules les pesées
    valides comptent, et l'effectif est relu dans le registre à la date de
    chaque pesée : une mortalité saisie après coup est prise en compte.
    """
    # Les instantanés des pesées annulées ne sont plus valables.
    IndicateurLot.objects.filter(
        pesee__lot_grossissement__bassin_id=bassin_id,
        pesee__statut=Pesee.Statut.ANNULEE,
    ).delete()

    lots = list(LotGrossissement.objects.filter(bassin_id=bassin_id))
    pesees = list(
        Pesee.objects.filter(
            lot_grossissement__bassin_id=bassin_id,
            statut=Pesee.Statut.VALIDEE,
        )
    )
    if not pesees:
        return

    date_max = max(pesee.date for pesee in pesees)
    effectif_a = _effectifs_a_la_date(bassin_id, lots, date_max)
    pesees_par_lot = defaultdict(list)
    for pesee in pesees:
        pesees_par_lot[pesee.lot_grossissement_id].append(
            PeseeCalcul(
                id=pesee.pk,
                date=pesee.date,
                effectif_vivant=max(
                    0, effectif_a(pesee.lot_grossissement_id, pesee.date)
                ),
                poids_moyen=pesee.poids_moyen,
            )
        )
    lots_calcul = [
        LotCalcul(
            id=lot.pk,
            date_entree=_date_entree(lot),
            effectif_initial=lot.effectif_initial,
            poids_initial=lot.poids_moyen_initial,
            date_cloture=lot.date_cloture,
            pesees=pesees_par_lot.get(lot.pk, []),
        )
        for lot in lots
    ]

    date_min = min(lot.date_entree for lot in lots_calcul)
    quantites_par_jour = defaultdict(Decimal)
    distributions = ConsommationProvende.objects.filter(
        bassin_id=bassin_id,
        statut=ConsommationProvende.Statut.VALIDE,
        date_distribution__gte=_debut_jour(date_min),
        date_distribution__lt=_debut_jour(date_max + timedelta(days=1)),
    ).values_list("date_distribution", "quantite_kg")
    for date_distribution, quantite_kg in distributions:
        quantites_par_jour[timezone.localtime(date_distribution).date()] += quantite_kg

    allocations = allouer_provende(quantites_par_jour, lots_calcul, effectif_a)
    for lot in lots_calcul:
        if not lot.pesees:
            continue
        for instantane in calculer_instantanes(lot, allocations[lot.id]):
            pesee_id = instantane.pop("pesee_id")
            IndicateurLot.objects.update_or_create(
                pesee_id=pesee_id,
                defaults=instantane,
            )


# --- Sortie de stock vers le grossissement (une seule transaction) ---------

POURCENTAGE_ECHANTILLON_DEFAUT = Decimal("10")
CHAMPS_REQUIS_VALIDATION = (
    "date_mise_en_place",
    "date_pesee_initiale",
    "nombre_poissons_peses",
    "poids_total_g",
    "effectif_entrant",
)


def proposer_code_lot(annee):
    """Propose le prochain code libre au format existant GR-AAAA-NNNN.

    Proposition indicative pour l'aperçu : si l'utilisateur la garde, le code
    définitif est généré par le modèle depuis l'identifiant (séquence jamais
    réutilisée, même après l'annulation d'une sortie qui supprime un lot).
    """
    numero = (LotGrossissement.objects.aggregate(dernier=Max("pk"))["dernier"] or 0) + 1
    while True:
        code = f"GR-{annee}-{numero:04d}"
        if not LotGrossissement.objects.filter(code=code).exists():
            return code
        numero += 1


def _verifier_date_saisie(jour, champ):
    aujourd_hui = timezone.localdate()
    if jour > aujourd_hui:
        raise ValidationError({champ: "La date ne peut pas être dans le futur."})
    # Même borne de saisie rétroactive que le journal de mortalité et de perte.
    limite = services_effectifs.JOURS_SAISIE_RETROACTIVE
    if limite is not None and jour < aujourd_hui - timedelta(days=limite):
        raise ValidationError({
            champ: "La date dépasse la période de saisie rétroactive autorisée."
        })


def _verifier_echantillon_initial(
    *,
    quantite_sortie,
    pourcentage,
    nombre,
    poids_total,
    effectif_entrant,
):
    if pourcentage <= 0 or pourcentage > 100:
        raise ValidationError({
            "pourcentage_echantillon": (
                "Le pourcentage doit être supérieur à 0 et inférieur ou égal à 100."
            )
        })
    cible = taille_echantillon_cible(quantite_sortie, pourcentage)
    conforme = None
    poids_moyen = None
    if nombre is not None:
        if nombre <= 0:
            raise ValidationError({
                "nombre_poissons_peses": (
                    "Le nombre de poissons pesés doit être strictement positif."
                )
            })
        if nombre > quantite_sortie:
            raise ValidationError({
                "nombre_poissons_peses": (
                    f"Le nombre de poissons pesés ({nombre}) dépasse la quantité "
                    f"sortie ({quantite_sortie})."
                )
            })
        if effectif_entrant is not None and nombre > effectif_entrant:
            raise ValidationError({
                "nombre_poissons_peses": (
                    f"Le nombre de poissons pesés ({nombre}) dépasse l'effectif "
                    f"entrant ({effectif_entrant})."
                )
            })
        conforme = echantillon_conforme(nombre, cible)
    if poids_total is not None and poids_total <= 0:
        raise ValidationError({
            "poids_total_g": "Le poids total doit être strictement positif."
        })
    if nombre is not None and poids_total is not None:
        poids_moyen = poids_moyen_echantillon(poids_total, nombre)
        if poids_moyen <= 0:
            raise ValidationError({
                "poids_total_g": (
                    "Le poids moyen calculé est nul, vérifiez le poids total."
                )
            })
    return {
        "taille_echantillon_cible": cible,
        "echantillon_conforme": conforme,
        "poids_moyen_initial": poids_moyen,
    }


def _verifier_code(code, code_propose):
    if not code:
        return code_propose
    longueur_max = LotGrossissement._meta.get_field("code").max_length
    if len(code) > longueur_max:
        raise ValidationError({
            "code": f"Le code ne doit pas dépasser {longueur_max} caractères."
        })
    if LotGrossissement.objects.filter(code=code).exists():
        raise ValidationError({"code": "Ce code de lot est déjà utilisé."})
    return code


def _preparer_sortie_grossissement(donnees, *, complet):
    """Contrôles communs à l'aperçu et à la validation, sans aucune écriture.

    `complet` : validation finale (tous les champs requis, échantillon non
    conforme refusé sans confirmation). Sinon, aperçu : les champs de la pesée
    et du lot sont facultatifs et contrôlés seulement s'ils sont fournis.
    Doit être appelée dans une transaction (verrous).
    """
    if complet:
        manquants = {
            champ: "Ce champ est obligatoire."
            for champ in CHAMPS_REQUIS_VALIDATION
            if donnees.get(champ) is None
        }
        if manquants:
            raise ValidationError(manquants)

    # Verrous : lot d'origine d'abord, puis bassins et hapas (preparer_sortie_stock).
    try:
        lot = Lot.objects.select_for_update().get(pk=donnees["id_lot"])
    except Lot.DoesNotExist as exc:
        raise ValidationError({"id_lot": "Lot introuvable."}) from exc
    if lot.stade != Lot.Stade.EN_STOCK:
        raise ValidationError({
            "id_lot": "Le lot d'origine doit être au stade EN_STOCK."
        })
    try:
        bassin = Bassin.objects.get(pk=donnees["id_bassin_destination"])
    except Bassin.DoesNotExist as exc:
        raise ValidationError({
            "id_bassin_destination": "Bassin introuvable."
        }) from exc

    date_mise_en_place = donnees.get("date_mise_en_place")
    date_pesee = donnees.get("date_pesee_initiale")
    date_reference = date_mise_en_place or timezone.localdate()
    if date_mise_en_place is not None:
        _verifier_date_saisie(date_mise_en_place, "date_mise_en_place")
    if date_pesee is not None:
        _verifier_date_saisie(date_pesee, "date_pesee_initiale")
        if date_pesee > date_reference:
            raise ValidationError({
                "date_pesee_initiale": (
                    "La pesée initiale ne peut pas être postérieure à la mise "
                    "en bassin."
                )
            })

    # Sortie totale : état du stock, hapas, bassin destination (verrouillé ici).
    preparation = preparer_sortie_stock(lot, bassin, date_reference)
    bassin = preparation["bassin_destination"]
    verifier_capacite_bassin(bassin, lot.espece, date_reference)
    quantite_sortie = preparation["effectif_sortant"]

    pourcentage = donnees.get("pourcentage_echantillon")
    if pourcentage is None:
        pourcentage = POURCENTAGE_ECHANTILLON_DEFAUT
    effectif_entrant = donnees.get("effectif_entrant")
    echantillon = _verifier_echantillon_initial(
        quantite_sortie=quantite_sortie,
        pourcentage=pourcentage,
        nombre=donnees.get("nombre_poissons_peses"),
        poids_total=donnees.get("poids_total_g"),
        effectif_entrant=effectif_entrant,
    )

    justification = (donnees.get("justification_ecart") or "").strip()
    if effectif_entrant is not None:
        verifier_ecart_sortie(quantite_sortie, effectif_entrant, justification)

    code_propose = proposer_code_lot(date_reference.year)
    code = _verifier_code((donnees.get("code") or "").strip(), code_propose)

    # En dernier : la confirmation n'a de sens qu'une fois les autres contrôles passés.
    if (
        complet
        and not echantillon["echantillon_conforme"]
        and not donnees.get("confirmer_echantillon_non_conforme", False)
    ):
        raise EchantillonNonConforme([{
            "id_lot": lot.pk,
            "reference_lot": lot.reference,
            "nombre_poissons_peses": donnees["nombre_poissons_peses"],
            "taille_echantillon_cible": echantillon["taille_echantillon_cible"],
        }])

    return {
        "lot": lot,
        "preparation": preparation,
        "bassin": bassin,
        "quantite_sortie": quantite_sortie,
        "date_mise_en_place": date_mise_en_place,
        "date_pesee_initiale": date_pesee,
        "pourcentage_echantillon": pourcentage,
        "nombre_poissons_peses": donnees.get("nombre_poissons_peses"),
        "poids_total_g": donnees.get("poids_total_g"),
        **echantillon,
        "effectif_entrant": effectif_entrant,
        "ecart_effectif": (
            effectif_entrant - quantite_sortie
            if effectif_entrant is not None
            else None
        ),
        "justification_ecart": justification,
        "code_propose": code_propose,
        "code": code,
        "observation": donnees.get("observation", ""),
    }


def apercu_sortie_grossissement(donnees):
    """Aperçu en lecture seule : mêmes contrôles que la validation, rien n'est écrit."""
    with transaction.atomic():
        contexte = _preparer_sortie_grossissement(donnees, complet=False)
        # Garantie supplémentaire : la transaction de l'aperçu est toujours annulée.
        transaction.set_rollback(True)
    return contexte


@transaction.atomic
def valider_sortie_grossissement(donnees, *, utilisateur):
    """Sortie totale du stock, mouvements, stade du lot et lot de grossissement.

    Toutes les validations précèdent les écritures ; une erreur à n'importe
    quelle étape annule tout (aucun mouvement, stock et stade inchangés,
    aucun lot de grossissement).
    """
    contexte = _preparer_sortie_grossissement(donnees, complet=True)
    lot = contexte["lot"]
    date_mise_en_place = timezone.make_aware(
        datetime.combine(contexte["date_mise_en_place"], time.min),
        timezone.get_current_timezone(),
    )

    # 1. Sortie du stock (clôture du stock) et mouvements SORTIE / ENTREE.
    sortie = enregistrer_sortie_stock_validee(
        lot=lot,
        preparation=contexte["preparation"],
        date_sortie=date_mise_en_place,
        effectif_entrant=contexte["effectif_entrant"],
        justification_ecart=contexte["justification_ecart"],
        utilisateur=utilisateur,
    )
    # 2. Lot d'origine en grossissement.
    lot.stade = Lot.Stade.EN_GROSSISSEMENT
    lot.save(update_fields=["stade", "updated_at"])
    # 3. Lot de grossissement, avec la trace de la pesée initiale.
    # Code proposé conservé (ou absent) : génération par le modèle, sans réutiliser
    # le code d'un lot supprimé. Seul un code réellement saisi est imposé.
    code = "" if contexte["code"] == contexte["code_propose"] else contexte["code"]
    try:
        with transaction.atomic():
            return LotGrossissement.objects.create(
                code=code,
                lot_origine=lot,
                sortie_stock=sortie,
                bassin=contexte["bassin"],
                espece=lot.espece,
                date_mise_en_place=date_mise_en_place,
                effectif_initial=contexte["effectif_entrant"],
                poids_moyen_initial=contexte["poids_moyen_initial"],
                date_pesee_initiale=contexte["date_pesee_initiale"],
                pourcentage_echantillon_initial=contexte["pourcentage_echantillon"],
                nombre_poissons_peses_initial=contexte["nombre_poissons_peses"],
                poids_total_initial_g=contexte["poids_total_g"],
                observation=contexte["observation"] or "",
                utilisateur=utilisateur,
            )
    except IntegrityError as exc:
        # Course entre deux validations sur le même code.
        if "code" in str(exc.__cause__ or exc):
            raise ValidationError({"code": "Ce code de lot est déjà utilisé."}) from exc
        raise
