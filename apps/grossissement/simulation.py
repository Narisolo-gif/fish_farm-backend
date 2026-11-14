"""Génération pure de données simulées de grossissement.

Aucun import Django ni accès à la base : le module renvoie des structures
simples (dates, quantités, valeurs en Decimal) que la commande
`seed_grossissement` transmet aux services de l'application.

Les valeurs utilisées ici (poids cibles, IC, taux de mortalité, paramètres
d'eau) sont des valeurs de données simulées, pas des seuils métier.
"""
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional


# Calendrier prévu : J30, J60, puis tous les 15 jours jusqu'à J180.
JOURS_PESEE = (30, 60, 75, 90, 105, 120, 135, 150, 165, 180)
POURCENTAGE_ECHANTILLON = Decimal("10")
BRUIT_POIDS = 0.03
BRUIT_PROVENDE = 0.05
IC_CIBLE = 1.6
TAUX_MORTALITE_180_JOURS = 0.03
HEURES_DISTRIBUTION = (time(8, 0), time(16, 0))
HEURE_PRELEVEMENT = time(7, 0)

CAUSE_PAR_DEFAUT = "Inconnue"
CAUSE_PIC = "Maladie"


def _decimal(valeur, decimales):
    return Decimal(str(valeur)).quantize(
        Decimal(1).scaleb(-decimales),
        rounding=ROUND_HALF_UP,
    )


# --- Définitions -----------------------------------------------------------

@dataclass(frozen=True)
class ProfilLot:
    cle: str
    espece: str
    effectif_initial: int
    poids_initial: Decimal
    poids_j180: Decimal
    exposant: float = 1.2
    facteur_gain: float = 1.0

    def poids_theorique(self, jour):
        """Poids moyen attendu (g) au jour de cycle, sans bruit."""
        progression = max(jour, 0) / 180
        gain = float(self.poids_j180 - self.poids_initial) * self.facteur_gain
        return float(self.poids_initial) + gain * progression ** self.exposant


@dataclass(frozen=True)
class DefinitionScenario:
    code: str
    duree: int
    lots: tuple
    description: str
    facteur_provende: float = 1.0
    jours_pesee: Optional[tuple] = None
    jour_pic_mortalite: Optional[int] = None
    trou_provende: Optional[tuple] = None  # (premier jour, nombre de jours)
    trou_parametres: Optional[tuple] = None
    jours_hors_plage: int = 0


def _carpe(effectif=2000, **kwargs):
    return ProfilLot("A", "CARPE", effectif, Decimal("20"), Decimal("250"), **kwargs)


SCENARIOS = {
    definition.code: definition
    for definition in (
        DefinitionScenario(
            "NORMAL", 185, (_carpe(),),
            "Cycle complet, croissance et mortalité normales",
        ),
        DefinitionScenario(
            "MORTALITE_ANORMALE", 100, (_carpe(),),
            "Pic de mortalité d'environ 10 % en 5 jours vers J70",
            jour_pic_mortalite=68,
        ),
        DefinitionScenario(
            "CROISSANCE_FAIBLE", 100, (_carpe(facteur_gain=0.5),),
            "Poids moyen environ 40 % sous la courbe normale",
        ),
        DefinitionScenario(
            "IC_ELEVE", 100, (_carpe(),),
            "Provende environ 60 % au-dessus du niveau normal",
            facteur_provende=1.6,
        ),
        DefinitionScenario(
            "PESEE_EN_RETARD", 100, (_carpe(),),
            "Pesées J30, J60 et J75 seulement : J90 n'est pas faite",
            jours_pesee=(30, 60, 75),
        ),
        DefinitionScenario(
            "PARAMETRE_HORS_PLAGE", 100, (_carpe(),),
            "Oxygène en forte baisse et eau plus chaude sur les 7 derniers jours",
            jours_hors_plage=7,
        ),
        DefinitionScenario(
            "DONNEES_MANQUANTES", 60, (_carpe(),),
            "Une pesée (J30), 10 jours sans provende, 15 jours sans paramètres",
            jours_pesee=(30,),
            trou_provende=(35, 10),
            trou_parametres=(38, 15),
        ),
        DefinitionScenario(
            "DEUX_LOTS", 100,
            (
                ProfilLot("A", "CARPE", 1000, Decimal("20"), Decimal("250"), exposant=1.2),
                ProfilLot("B", "TILAPIA", 500, Decimal("40"), Decimal("220"), exposant=1.0),
            ),
            "Deux espèces dans un bassin, provende saisie par bassin",
        ),
    )
}
CODES_SCENARIOS = tuple(SCENARIOS)


# --- Résultats --------------------------------------------------------------

@dataclass(frozen=True)
class MortaliteSimulee:
    date: date
    quantite: int
    cause: str


@dataclass(frozen=True)
class PeseeSimulee:
    date: date
    jour_de_cycle: int
    effectif_vivant: int
    nombre_poissons_peses: int
    poids_total_g: Decimal
    poids_moyen_g: Decimal


@dataclass(frozen=True)
class DistributionSimulee:
    """Distribution du bassin : jamais rattachée à un lot."""

    date_heure: datetime
    quantite_kg: Decimal


@dataclass(frozen=True)
class ParametreSimule:
    date_heure: datetime
    temperature_eau: Decimal
    temperature_air: Decimal
    oxygene: Decimal
    ph: Decimal
    nitrite: Decimal
    nitrate: Decimal
    ammoniac: Decimal


@dataclass
class LotSimule:
    profil: ProfilLot
    date_entree: date
    mortalites: list = field(default_factory=list)
    pesees: list = field(default_factory=list)

    def effectif_a(self, jour_date):
        morts = sum(m.quantite for m in self.mortalites if m.date <= jour_date)
        return self.profil.effectif_initial - morts


@dataclass
class ScenarioSimule:
    definition: DefinitionScenario
    date_reference: date
    date_entree: date
    lots: list
    distributions: list
    parametres: list

    @property
    def code(self):
        return self.definition.code

    def dates_pesee(self):
        return sorted({p.date for lot in self.lots for p in lot.pesees})

    def statistiques(self):
        return {
            "lots": len(self.lots),
            "pesees": len(self.dates_pesee()),
            "lignes_pesee": sum(len(lot.pesees) for lot in self.lots),
            "distributions": len(self.distributions),
            "provende_kg": sum((d.quantite_kg for d in self.distributions), Decimal("0")),
            "mortalites": sum(len(lot.mortalites) for lot in self.lots),
            "poissons_morts": sum(m.quantite for lot in self.lots for m in lot.mortalites),
            "parametres": len(self.parametres),
        }


# --- Générateurs ------------------------------------------------------------

def calendrier_pesees(date_entree, date_reference, jours=JOURS_PESEE):
    """Dates de pesée prévues, limitées à la date de référence."""
    return [
        date_entree + timedelta(days=jour)
        for jour in jours
        if date_entree + timedelta(days=jour) <= date_reference
    ]


def courbe_poids(profil, jours, rng):
    """Poids moyen mesuré (g) aux jours donnés : courbe théorique ± 3 %."""
    return {
        jour: profil.poids_theorique(jour) * (1 + rng.uniform(-BRUIT_POIDS, BRUIT_POIDS))
        for jour in jours
    }


def serie_mortalites(profil, date_entree, duree, rng, *, jour_pic=None):
    """Mortalités faibles et espacées, plus un pic éventuel de 10 % en 5 jours."""
    total = round(profil.effectif_initial * TAUX_MORTALITE_180_JOURS * duree / 180)
    pic = set(range(jour_pic, jour_pic + 5)) if jour_pic is not None else set()
    jours = []
    jour = rng.randint(5, 10)
    while jour < duree:
        if jour not in pic:
            jours.append(jour)
        jour += rng.randint(7, 12)
    mortalites = []
    if jours and total > 0:
        poids = [rng.uniform(0.6, 1.4) for _ in jours]
        somme = sum(poids)
        quantites = [max(1, round(total * p / somme)) for p in poids]
        for jour, quantite in zip(jours, quantites):
            mortalites.append(
                MortaliteSimulee(date_entree + timedelta(days=jour), quantite, CAUSE_PAR_DEFAUT)
            )
    if jour_pic is not None:
        avant_pic = profil.effectif_initial - sum(
            m.quantite for m in mortalites
            if m.date < date_entree + timedelta(days=jour_pic)
        )
        total_pic = round(avant_pic * 0.10)
        repartition = (0.15, 0.25, 0.25, 0.20, 0.15)
        quantites = [round(total_pic * part) for part in repartition]
        quantites[-1] += total_pic - sum(quantites)
        for decalage, quantite in enumerate(quantites):
            mortalites.append(
                MortaliteSimulee(
                    date_entree + timedelta(days=jour_pic + decalage),
                    quantite,
                    CAUSE_PIC,
                )
            )
    mortalites.sort(key=lambda m: m.date)
    # Garde-fou : l'effectif vivant ne devient jamais négatif (il reste au moins 1 poisson).
    restant = profil.effectif_initial
    resultat = []
    for mortalite in mortalites:
        quantite = min(mortalite.quantite, restant - 1)
        if quantite > 0:
            resultat.append(MortaliteSimulee(mortalite.date, quantite, mortalite.cause))
            restant -= quantite
    return resultat


def serie_pesees(lot, jours_pesee, rng):
    """Pesées sur 10 % de l'effectif vivant, dans l'ordre chronologique."""
    poids_mesures = courbe_poids(lot.profil, jours_pesee, rng)
    pesees = []
    for jour in sorted(jours_pesee):
        date_pesee = lot.date_entree + timedelta(days=jour)
        effectif = lot.effectif_a(date_pesee)
        nombre = min(
            effectif,
            max(1, math.ceil(effectif * POURCENTAGE_ECHANTILLON / 100)),
        )
        poids_total = _decimal(nombre * poids_mesures[jour], 2)
        pesees.append(
            PeseeSimulee(
                date=date_pesee,
                jour_de_cycle=jour,
                effectif_vivant=effectif,
                nombre_poissons_peses=nombre,
                poids_total_g=poids_total,
                poids_moyen_g=_decimal(poids_total / nombre, 2),
            )
        )
    return pesees


def serie_distributions(definition, lots, date_entree, rng):
    """Provende du bassin : quantité journalière = IC cible × gain des vivants.

    Deux distributions par jour (au plus 3 autorisées), sur les jours
    strictement antérieurs à la date de référence.
    """
    trou = set()
    if definition.trou_provende:
        debut, longueur = definition.trou_provende
        trou = set(range(debut, debut + longueur))
    distributions = []
    for jour in range(definition.duree):
        if jour in trou:
            continue
        jour_date = date_entree + timedelta(days=jour)
        gain_kg = sum(
            lot.effectif_a(jour_date)
            * (lot.profil.poids_theorique(jour + 1) - lot.profil.poids_theorique(jour))
            / 1000
            for lot in lots
        )
        quantite_jour = (
            gain_kg * IC_CIBLE * definition.facteur_provende
            * (1 + rng.uniform(-BRUIT_PROVENDE, BRUIT_PROVENDE))
        )
        premiere = max(Decimal("0.01"), _decimal(quantite_jour / 2, 2))
        seconde = max(Decimal("0.01"), _decimal(quantite_jour, 2) - premiere)
        for heure, quantite in zip(HEURES_DISTRIBUTION, (premiere, seconde)):
            distributions.append(
                DistributionSimulee(
                    datetime.combine(jour_date, heure, tzinfo=timezone.utc),
                    quantite,
                )
            )
    return distributions


def serie_parametres(definition, date_entree, rng):
    """Un prélèvement par jour ; valeurs plausibles pour carpe et tilapia.

    Unités supposées : °C pour les températures, mg/L pour l'oxygène et les
    composés azotés (le modèle ne précise pas d'unité).
    """
    trou = set()
    if definition.trou_parametres:
        debut, longueur = definition.trou_parametres
        trou = set(range(debut, debut + longueur))
    debut_hors_plage = definition.duree - definition.jours_hors_plage
    parametres = []
    for jour in range(definition.duree):
        if jour in trou:
            continue
        hors_plage = definition.jours_hors_plage and jour >= debut_hors_plage
        temperature_eau = rng.gauss(32.5, 0.4) if hors_plage else rng.gauss(27.0, 0.6)
        oxygene = rng.gauss(2.6, 0.25) if hors_plage else rng.gauss(6.5, 0.3)
        parametres.append(
            ParametreSimule(
                date_heure=datetime.combine(
                    date_entree + timedelta(days=jour), HEURE_PRELEVEMENT, tzinfo=timezone.utc
                ),
                temperature_eau=_decimal(temperature_eau, 2),
                temperature_air=_decimal(rng.gauss(29.0, 1.2), 2),
                oxygene=_decimal(max(0.5, oxygene), 2),
                ph=_decimal(rng.gauss(7.2, 0.15), 2),
                nitrite=_decimal(max(0.0, rng.gauss(0.05, 0.015)), 2),
                nitrate=_decimal(max(0.0, rng.gauss(5.0, 0.8)), 2),
                ammoniac=_decimal(max(0.0, rng.gauss(0.10, 0.03)), 2),
            )
        )
    return parametres


def construire_scenario(code, date_reference, seed=42):
    """Scénario complet, reproductible pour une même graine et une même date."""
    definition = SCENARIOS[code]
    # Générateur local, propre au scénario : une sélection de scénarios
    # produit les mêmes données qu'une exécution complète.
    rng = random.Random(f"{seed}:{code}")
    date_entree = date_reference - timedelta(days=definition.duree)
    lots = []
    for profil in definition.lots:
        lot = LotSimule(profil=profil, date_entree=date_entree)
        lot.mortalites = serie_mortalites(
            profil, date_entree, definition.duree, rng,
            jour_pic=definition.jour_pic_mortalite,
        )
        jours = definition.jours_pesee or JOURS_PESEE
        lot.pesees = serie_pesees(
            lot,
            [j for j in jours if date_entree + timedelta(days=j) <= date_reference],
            rng,
        )
        lots.append(lot)
    return ScenarioSimule(
        definition=definition,
        date_reference=date_reference,
        date_entree=date_entree,
        lots=lots,
        distributions=serie_distributions(definition, lots, date_entree, rng),
        parametres=serie_parametres(definition, date_entree, rng),
    )


def construire_scenarios(codes, date_reference, seed=42):
    inconnus = [code for code in codes if code not in SCENARIOS]
    if inconnus:
        raise ValueError(
            f"Scénario(s) inconnu(s) : {', '.join(inconnus)}. "
            f"Disponibles : {', '.join(CODES_SCENARIOS)}."
        )
    return [construire_scenario(code, date_reference, seed) for code in codes]
