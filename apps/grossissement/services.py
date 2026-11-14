from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.bassins.models import Bassin, Hapa
from apps.effectifs.calculs import effectif_vivant
from apps.effectifs.models import MouvementEffectif
from apps.effectifs.services import enregistrer_mouvement_operation
from apps.lots.models import Lot

from .models import LotGrossissement, Pesee, PeseeLot


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
            "date_sortie": (
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


def _calculer_cible(effectif, pourcentage):
    cible = (
        Decimal(effectif) * Decimal(pourcentage) / Decimal(100)
    ).to_integral_value(rounding=ROUND_CEILING)
    return max(1, int(cible))


def _calculer_poids_moyen(poids_total_g, nombre_poissons_peses):
    return (
        Decimal(poids_total_g) / Decimal(nombre_poissons_peses)
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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
    return pesee


@transaction.atomic
def supprimer_pesee(pesee_id):
    pesee, lignes = _verrouiller_pesee(pesee_id)
    _verifier_pesee_modifiable(pesee, lignes)
    pesee.delete()
