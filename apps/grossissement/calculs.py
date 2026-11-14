"""Calculs purs des indicateurs de grossissement.

Aucun accès à la base ni import Django : les fonctions reçoivent des valeurs
simples et renvoient des Decimal. L'arrondi au demi supérieur n'est appliqué
qu'au résultat final, jamais aux valeurs intermédiaires. Les valeurs non
calculables valent None, jamais zéro.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Optional


ZERO = Decimal("0")
MILLE = Decimal("1000")


def _arrondir(valeur, decimales):
    return Decimal(valeur).quantize(
        Decimal(1).scaleb(-decimales),
        rounding=ROUND_HALF_UP,
    )


def _biomasse_exacte(effectif_vivant, poids_moyen_g):
    """Biomasse en kg sans arrondi, pour les calculs intermédiaires."""
    return Decimal(effectif_vivant) * Decimal(poids_moyen_g) / MILLE


def biomasse_kg(effectif_vivant, poids_moyen_g):
    """Biomasse vivante en kg, arrondie à 3 décimales."""
    return _arrondir(_biomasse_exacte(effectif_vivant, poids_moyen_g), 3)


def gmq(poids_moyen_debut, poids_moyen_fin, nombre_jours):
    """Gain moyen quotidien (g/j par poisson), arrondi à 2 décimales.

    None si la période ne compte aucun jour. Un résultat négatif est valide.
    """
    if nombre_jours is None or nombre_jours <= 0:
        return None
    return _arrondir(
        (Decimal(poids_moyen_fin) - Decimal(poids_moyen_debut))
        / Decimal(nombre_jours),
        2,
    )


def ic(provende_kg, biomasse_debut_kg, biomasse_fin_kg):
    """Indice de consommation, arrondi à 2 décimales.

    None si le gain de biomasse est nul ou négatif, ou si aucune provende
    n'a été distribuée (indicateur non interprétable).
    """
    provende = Decimal(provende_kg)
    gain = Decimal(biomasse_fin_kg) - Decimal(biomasse_debut_kg)
    if gain <= 0 or provende == 0:
        return None
    return _arrondir(provende / gain, 2)


def repartir_provende(quantite_kg, biomasses_reference):
    """Répartit une quantité (kg) au prorata des biomasses de référence.

    Chaque part est arrondie à 3 décimales, sauf celle du lot de plus grande
    biomasse (à égalité : le plus petit identifiant), qui reçoit le reste :
    la somme des parts est exactement égale à la quantité. Si la somme des
    biomasses est nulle, rien n'est alloué.
    """
    quantite = Decimal(quantite_kg)
    biomasses = {
        lot_id: Decimal(biomasse)
        for lot_id, biomasse in biomasses_reference.items()
    }
    total = sum(biomasses.values(), ZERO)
    if total <= 0:
        return {}
    principal = min(biomasses, key=lambda lot_id: (-biomasses[lot_id], lot_id))
    parts = {
        lot_id: _arrondir(quantite * biomasse / total, 3)
        for lot_id, biomasse in biomasses.items()
        if lot_id != principal
    }
    parts[principal] = quantite - sum(parts.values(), ZERO)
    return parts


@dataclass(frozen=True)
class PeseeCalcul:
    id: object
    date: date
    effectif_vivant: int
    poids_moyen: Decimal


@dataclass
class LotCalcul:
    id: object
    date_entree: date
    effectif_initial: int
    poids_initial: Optional[Decimal]
    date_cloture: Optional[date] = None
    pesees: list = field(default_factory=list)

    def pesees_triees(self):
        return sorted(self.pesees, key=lambda pesee: pesee.date)

    def poids_reference(self, jour):
        """Dernier poids moyen connu strictement avant le jour, sinon le poids initial."""
        poids = self.poids_initial
        for pesee in self.pesees_triees():
            if pesee.date >= jour:
                break
            poids = pesee.poids_moyen
        return poids


def allouer_provende(
    quantites_par_jour,
    lots,
    effectif_a: Callable[[object, date], int],
):
    """Alloue jour par jour la provende du bassin aux lots présents.

    `quantites_par_jour` : {date locale: quantité totale du jour en kg}.
    `effectif_a(lot_id, jour)` : effectif vivant du lot à ce jour (registre).
    Retourne {lot_id: {jour: part en kg}}.
    """
    allocations = {lot.id: {} for lot in lots}
    for jour in sorted(quantites_par_jour):
        quantite = Decimal(quantites_par_jour[jour])
        if quantite <= 0:
            continue
        references = {}
        for lot in lots:
            if lot.date_entree > jour:
                continue
            if lot.date_cloture is not None and lot.date_cloture < jour:
                continue
            poids = lot.poids_reference(jour)
            if poids is None:
                # Lot ancien sans poids initial ni pesée : référence inconnue.
                continue
            effectif = max(0, effectif_a(lot.id, jour))
            references[lot.id] = _biomasse_exacte(effectif, poids)
        for lot_id, part in repartir_provende(quantite, references).items():
            allocations[lot_id][jour] = part
    return allocations


def calculer_instantanes(lot, parts_par_jour):
    """Instantanés d'indicateurs du lot, une entrée par pesée (ordre chronologique).

    `parts_par_jour` : {jour: part de provende allouée au lot en kg}.
    """
    poids_initial = (
        Decimal(lot.poids_initial) if lot.poids_initial is not None else None
    )
    biomasse_0 = (
        _biomasse_exacte(lot.effectif_initial, poids_initial)
        if poids_initial is not None
        else None
    )
    date_precedente = lot.date_entree
    poids_precedent = poids_initial
    biomasse_precedente = biomasse_0
    provende_cumulee = ZERO
    instantanes = []
    for rang, pesee in enumerate(lot.pesees_triees()):
        if rang == 0:
            dans_periode = lambda jour, fin=pesee.date: lot.date_entree <= jour <= fin
        else:
            dans_periode = (
                lambda jour, debut=date_precedente, fin=pesee.date: debut < jour <= fin
            )
        provende = sum(
            (Decimal(part) for jour, part in parts_par_jour.items() if dans_periode(jour)),
            ZERO,
        )
        provende_cumulee += provende
        poids = Decimal(pesee.poids_moyen)
        biomasse = _biomasse_exacte(pesee.effectif_vivant, poids)
        jour_de_cycle = (pesee.date - lot.date_entree).days
        instantanes.append({
            "pesee_id": pesee.id,
            "effectif_vivant": pesee.effectif_vivant,
            "poids_moyen": poids,
            "biomasse_kg": _arrondir(biomasse, 3),
            "gmq_periode": (
                gmq(poids_precedent, poids, (pesee.date - date_precedente).days)
                if poids_precedent is not None
                else None
            ),
            "gmq_cumule": (
                gmq(poids_initial, poids, jour_de_cycle)
                if poids_initial is not None
                else None
            ),
            "provende_periode_kg": _arrondir(provende, 3),
            "ic_periode": (
                ic(provende, biomasse_precedente, biomasse)
                if biomasse_precedente is not None
                else None
            ),
            "ic_cumule": (
                ic(provende_cumulee, biomasse_0, biomasse)
                if biomasse_0 is not None
                else None
            ),
            "jour_de_cycle": jour_de_cycle,
        })
        date_precedente = pesee.date
        poids_precedent = poids
        biomasse_precedente = biomasse
    return instantanes
