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
    echantillon_conforme,
    poids_moyen_echantillon,
    taille_echantillon_cible,
)
from .models import IndicateurLot, LotGrossissement, Pesee, PeseeLot


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
    # TODO : inclure les répartitions de provende.
    return lot.lignes_pesee.exists()

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


def _controler_lot_pour_pesee(lot, bassin, date_pesee):
    if lot.bassin_id != bassin.pk:
        raise ValidationError({
            "id_lot_grossissement": (
                f"Lot {lot.code} : le lot n'appartient pas au bassin de la pesée."
            )
        })
    if lot.statut != LotGrossissement.Statut.ACTIF:
        raise ValidationError({
            "id_lot_grossissement": (
                f"Lot {lot.code} : seul un lot de grossissement actif peut être pesé."
            )
        })
    if date_pesee < _date_entree(lot):
        raise ValidationError({
            "date": (
                f"Lot {lot.code} : la date de pesée ne peut pas précéder "
                f"la mise en place du lot ({_date_entree(lot)})."
            )
        })


def _calculer_ligne(lot, date_pesee, *, pourcentage, nombre, poids_total):
    """Contrôle une ligne et renvoie ses valeurs calculées."""
    if pourcentage is None:
        pourcentage = lot.pourcentage_groupe_pesee
    if pourcentage <= 0 or pourcentage > 100:
        raise ValidationError({
            "pourcentage_echantillon": (
                f"Lot {lot.code} : le pourcentage doit être supérieur à 0 "
                "et inférieur ou égal à 100."
            )
        })
    if nombre is None or nombre <= 0:
        raise ValidationError({
            "nombre_poissons_peses": (
                f"Lot {lot.code} : le nombre de poissons pesés doit être "
                "strictement positif."
            )
        })
    if poids_total is None or poids_total <= 0:
        raise ValidationError({
            "poids_total_g": (
                f"Lot {lot.code} : le poids total doit être strictement positif."
            )
        })
    effectif = effectif_vivant(lot.bassin_id, lot.espece, a_la_date=date_pesee)
    if effectif <= 0:
        raise ValidationError({
            "effectif_vivant": (
                f"Lot {lot.code} : l'effectif vivant est nul à la date de la "
                "pesée, aucune pesée n'est possible."
            )
        })
    if nombre > effectif:
        raise ValidationError({
            "nombre_poissons_peses": (
                f"Lot {lot.code} : le nombre de poissons pesés ({nombre}) dépasse "
                f"l'effectif vivant ({effectif})."
            )
        })
    poids_moyen = _calculer_poids_moyen(poids_total, nombre)
    if poids_moyen <= 0:
        raise ValidationError({
            "poids_total_g": (
                f"Lot {lot.code} : le poids moyen calculé est nul, "
                "vérifiez le poids total."
            )
        })
    return {
        "pourcentage_echantillon": pourcentage,
        "effectif_vivant": effectif,
        "taille_echantillon_cible": _calculer_cible(effectif, pourcentage),
        "nombre_poissons_peses": nombre,
        "poids_total_g": poids_total,
        "poids_moyen": poids_moyen,
    }


def _verifier_conformite(lignes_calculees, confirmer):
    non_conformes = [
        {
            "id_lot_grossissement": lot.pk,
            "reference_lot": lot.code,
            "nombre_poissons_peses": valeurs["nombre_poissons_peses"],
            "taille_echantillon_cible": valeurs["taille_echantillon_cible"],
        }
        for lot, valeurs in lignes_calculees
        if valeurs["nombre_poissons_peses"] < valeurs["taille_echantillon_cible"]
    ]
    if non_conformes and not confirmer:
        raise EchantillonNonConforme(non_conformes)


def _verifier_lots_uniques(identifiants):
    if len(set(identifiants)) != len(identifiants):
        raise ValidationError({
            "lignes": "Un même lot ne peut figurer qu'une fois dans une pesée."
        })


@transaction.atomic
def creer_pesee(
    *,
    bassin_id,
    date_pesee,
    lignes,
    utilisateur,
    observation="",
    confirmer=False,
):
    try:
        bassin = Bassin.objects.select_for_update().get(pk=bassin_id)
    except Bassin.DoesNotExist as exc:
        raise ValidationError({"id_bassin": "Bassin introuvable."}) from exc
    if bassin.statut != Bassin.Statut.ACTIF:
        raise ValidationError({"id_bassin": "Le bassin doit être actif."})
    if bassin.categorie != Bassin.Categorie.GROSSISSEMENT:
        raise ValidationError({
            "id_bassin": "Le bassin doit être de catégorie GROSSISSEMENT."
        })
    if date_pesee > timezone.localdate():
        raise ValidationError({
            "date": "La date de pesée ne peut pas être dans le futur."
        })
    if not lignes:
        raise ValidationError({"lignes": "Au moins un lot doit être pesé."})
    identifiants = [ligne["id_lot_grossissement"] for ligne in lignes]
    _verifier_lots_uniques(identifiants)

    lots = {
        lot.pk: lot
        for lot in LotGrossissement.objects.select_for_update()
        .filter(pk__in=identifiants)
        .order_by("pk")
    }
    lignes_calculees = []
    for donnees in lignes:
        lot = lots.get(donnees["id_lot_grossissement"])
        if lot is None:
            raise ValidationError({
                "id_lot_grossissement": (
                    "Lot de grossissement introuvable "
                    f"({donnees['id_lot_grossissement']})."
                )
            })
        _controler_lot_pour_pesee(lot, bassin, date_pesee)
        derniere = (
            PeseeLot.objects.filter(
                lot_grossissement=lot,
                pesee__date__gte=date_pesee,
            )
            .order_by("-pesee__date")
            .values_list("pesee__date", flat=True)
            .first()
        )
        if derniere is not None:
            raise ValidationError({
                "date": (
                    f"Lot {lot.code} : la date doit être postérieure à la "
                    f"dernière pesée du lot ({derniere})."
                )
            })
        lignes_calculees.append((
            lot,
            _calculer_ligne(
                lot,
                date_pesee,
                pourcentage=donnees.get("pourcentage_echantillon"),
                nombre=donnees.get("nombre_poissons_peses"),
                poids_total=donnees.get("poids_total_g"),
            ),
        ))
    _verifier_conformite(lignes_calculees, confirmer)

    pesee = Pesee.objects.create(
        bassin=bassin,
        date=date_pesee,
        utilisateur=utilisateur,
        observation=observation or "",
    )
    PeseeLot.objects.bulk_create([
        PeseeLot(pesee=pesee, lot_grossissement=lot, **valeurs)
        for lot, valeurs in lignes_calculees
    ])
    recalculer_indicateurs_bassin(bassin.pk)
    return pesee


def _verrouiller_pesee(pesee_id):
    try:
        pesee = Pesee.objects.select_for_update().get(pk=pesee_id)
    except Pesee.DoesNotExist as exc:
        raise NotFound("Pesée introuvable.") from exc
    Bassin.objects.select_for_update().get(pk=pesee.bassin_id)
    lignes = list(pesee.lignes.order_by("id"))
    lots = {
        lot.pk: lot
        for lot in LotGrossissement.objects.select_for_update()
        .filter(pk__in=[ligne.lot_grossissement_id for ligne in lignes])
        .order_by("pk")
    }
    for ligne in lignes:
        ligne.lot_grossissement = lots[ligne.lot_grossissement_id]
    return pesee, lignes


def _verifier_pesee_modifiable(pesee, lignes):
    """Seule la dernière pesée de chacun de ses lots, tous actifs, est corrigeable."""
    for ligne in lignes:
        lot = ligne.lot_grossissement
        if lot.statut != LotGrossissement.Statut.ACTIF:
            raise ValidationError({
                "detail": (
                    f"Lot {lot.code} : le lot n'est plus actif, "
                    "la pesée ne peut plus être modifiée ni supprimée."
                )
            })
        if PeseeLot.objects.filter(
            lot_grossissement=lot,
            pesee__date__gt=pesee.date,
        ).exists():
            raise ValidationError({
                "detail": (
                    f"Lot {lot.code} : une pesée plus récente existe, seule la "
                    "dernière pesée du lot peut être modifiée ou supprimée."
                )
            })


@transaction.atomic
def modifier_pesee(pesee_id, donnees, *, confirmer=False):
    pesee, lignes = _verrouiller_pesee(pesee_id)
    _verifier_pesee_modifiable(pesee, lignes)

    modifications = donnees.get("lignes") or []
    _verifier_lots_uniques([m["id_lot_grossissement"] for m in modifications])
    lignes_par_lot = {ligne.lot_grossissement_id: ligne for ligne in lignes}

    lignes_calculees = []
    lignes_modifiees = []
    for modification in modifications:
        ligne = lignes_par_lot.get(modification["id_lot_grossissement"])
        if ligne is None:
            raise ValidationError({
                "id_lot_grossissement": (
                    "Ce lot ne fait pas partie de la pesée "
                    f"({modification['id_lot_grossissement']})."
                )
            })
        lot = ligne.lot_grossissement
        valeurs = _calculer_ligne(
            lot,
            pesee.date,
            pourcentage=modification.get(
                "pourcentage_echantillon", ligne.pourcentage_echantillon
            ),
            nombre=modification.get(
                "nombre_poissons_peses", ligne.nombre_poissons_peses
            ),
            poids_total=modification.get("poids_total_g", ligne.poids_total_g),
        )
        lignes_calculees.append((lot, valeurs))
        lignes_modifiees.append((ligne, valeurs))
    _verifier_conformite(lignes_calculees, confirmer)

    for ligne, valeurs in lignes_modifiees:
        for champ, valeur in valeurs.items():
            setattr(ligne, champ, valeur)
        ligne.save(update_fields=list(valeurs))
    if "observation" in donnees:
        pesee.observation = donnees["observation"] or ""
    pesee.save(update_fields=["observation", "updated_at"])
    recalculer_indicateurs_bassin(pesee.bassin_id)
    return pesee


@transaction.atomic
def supprimer_pesee(pesee_id):
    pesee, lignes = _verrouiller_pesee(pesee_id)
    _verifier_pesee_modifiable(pesee, lignes)
    bassin_id = pesee.bassin_id
    # Les instantanés de la pesée disparaissent avec ses lignes (CASCADE).
    pesee.delete()
    recalculer_indicateurs_bassin(bassin_id)


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
    poids et des effectifs des autres lots présents.
    """
    lots = list(LotGrossissement.objects.filter(bassin_id=bassin_id))
    lignes = list(
        PeseeLot.objects.filter(lot_grossissement__bassin_id=bassin_id)
        .select_related("pesee")
    )
    if not lignes:
        return

    pesees_par_lot = defaultdict(list)
    for ligne in lignes:
        pesees_par_lot[ligne.lot_grossissement_id].append(
            PeseeCalcul(
                id=ligne.pk,
                date=ligne.pesee.date,
                effectif_vivant=ligne.effectif_vivant,
                poids_moyen=ligne.poids_moyen,
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
    date_max = max(ligne.pesee.date for ligne in lignes)
    quantites_par_jour = defaultdict(Decimal)
    distributions = ConsommationProvende.objects.filter(
        bassin_id=bassin_id,
        statut=ConsommationProvende.Statut.VALIDE,
        date_distribution__gte=_debut_jour(date_min),
        date_distribution__lt=_debut_jour(date_max + timedelta(days=1)),
    ).values_list("date_distribution", "quantite_kg")
    for date_distribution, quantite_kg in distributions:
        quantites_par_jour[timezone.localtime(date_distribution).date()] += quantite_kg

    allocations = allouer_provende(
        quantites_par_jour,
        lots_calcul,
        _effectifs_a_la_date(bassin_id, lots, date_max),
    )
    for lot in lots_calcul:
        if not lot.pesees:
            continue
        for instantane in calculer_instantanes(lot, allocations[lot.id]):
            pesee_lot_id = instantane.pop("pesee_id")
            IndicateurLot.objects.update_or_create(
                pesee_lot_id=pesee_lot_id,
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
