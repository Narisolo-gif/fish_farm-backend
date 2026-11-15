"""Calculs purs des indicateurs de grossissement.

Aucun accès à la base ni import Django : les fonctions reçoivent des valeurs
simples et renvoient des Decimal. L'arrondi au demi supérieur n'est appliqué
qu'au résultat final, jamais aux valeurs intermédiaires. Les valeurs non
calculables valent None, jamais zéro.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Callable, Optional


ZERO = Decimal("0")
MILLE = Decimal("1000")

# Constantes métier du suivi : seules valeurs à modifier pour changer le
# calendrier, le seuil de vente ou la tolérance de retard.
PREMIERES_ECHEANCES_JOURS = (30, 60)
INTERVALLE_PESEE_JOURS = 15
TOLERANCE_RETARD_PESEE_JOURS = 0
POIDS_VENTE_G = 250


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


def taille_echantillon_cible(effectif, pourcentage):
    """Nombre de poissons à peser : arrondi supérieur de pourcentage × effectif / 100, minimum 1."""
    cible = (
        Decimal(effectif) * Decimal(pourcentage) / Decimal(100)
    ).to_integral_value(rounding=ROUND_CEILING)
    return max(1, int(cible))


def poids_moyen_echantillon(poids_total_g, nombre_poissons):
    """Poids moyen (g) d'un échantillon, arrondi au demi supérieur à 2 décimales."""
    return _arrondir(Decimal(poids_total_g) / Decimal(nombre_poissons), 2)


def echantillon_conforme(nombre_poissons_peses, taille_cible):
    """Vrai si l'échantillon atteint la taille cible."""
    return nombre_poissons_peses >= taille_cible


def pourcentage_reel_echantillon(nombre_poissons_peses, effectif_vivant):
    """Part réellement pesée de l'effectif vivant (%), arrondie à 2 décimales."""
    return _arrondir(
        Decimal(nombre_poissons_peses) * Decimal(100) / Decimal(effectif_vivant),
        2,
    )


# --- Calendrier des pesées, seuil et alertes (information, jamais bloquant) --

def jour_echeance(rang):
    """Jour de cycle de la n-ième pesée prévue : J30, J60, puis tous les 15 jours."""
    if rang <= len(PREMIERES_ECHEANCES_JOURS):
        return PREMIERES_ECHEANCES_JOURS[rang - 1]
    return (
        PREMIERES_ECHEANCES_JOURS[-1]
        + (rang - len(PREMIERES_ECHEANCES_JOURS)) * INTERVALLE_PESEE_JOURS
    )


def date_echeance(date_entree, rang):
    return date_entree + timedelta(days=jour_echeance(rang))


def etat_calendrier(
    date_entree,
    date_reference,
    date_derniere_pesee=None,
    tolerance_jours=TOLERANCE_RETARD_PESEE_JOURS,
):
    """Position du lot dans le calendrier des pesées, sans limite de durée.

    - `etape_courante` : 1 avant J30, 2 de J30 à J59, 3 de J60 à J74, etc. ;
    - `prochaine_pesee` : première échéance strictement postérieure à la
      dernière pesée réalisée (J30 s'il n'y en a pas) : une échéance sautée
      puis rattrapée ne laisse pas de faux retard ;
    - retard : la date de référence dépasse la date prévue plus la tolérance ;
    - `pesees_prevues` : échéances datées au plus tard de la date de référence.
    """
    age = max(0, (date_reference - date_entree).days)
    etape = 1
    while jour_echeance(etape) <= age:
        etape += 1

    reference = date_derniere_pesee or date_entree
    rang = 1
    while date_echeance(date_entree, rang) <= reference:
        rang += 1
    date_prevue = date_echeance(date_entree, rang)
    en_retard = date_reference > date_prevue + timedelta(days=tolerance_jours)

    pesees_prevues = []
    numero = 1
    while date_echeance(date_entree, numero) <= date_reference:
        pesees_prevues.append({
            "numero": numero,
            "jour_cible": jour_echeance(numero),
            "date_prevue": date_echeance(date_entree, numero),
        })
        numero += 1

    return {
        "etape_courante": etape,
        "prochaine_pesee": {
            "numero": rang,
            "jour_cible": jour_echeance(rang),
            "date_prevue": date_prevue,
            "jours_restants": max(0, (date_prevue - date_reference).days),
            "en_retard": en_retard,
            "jours_de_retard": (
                (date_reference - date_prevue).days if en_retard else 0
            ),
        },
        "pesees_prevues": pesees_prevues,
    }


def etat_seuil(pesees, seuil_g=POIDS_VENTE_G):
    """Seuil de vente : atteint, définitivement, dès qu'une pesée l'atteint.

    `pesees` : objets ayant `date` et `poids_moyen`, dans un ordre quelconque.
    Renvoie aussi la dernière pesée au seuil, citée par l'alerte.
    """
    au_seuil = sorted(
        (pesee for pesee in pesees if Decimal(pesee.poids_moyen) >= Decimal(seuil_g)),
        key=lambda pesee: pesee.date,
    )
    return {
        "atteint": bool(au_seuil),
        "date_premiere_pesee_au_seuil": au_seuil[0].date if au_seuil else None,
        "derniere_pesee_au_seuil": au_seuil[-1] if au_seuil else None,
    }


def _nombre_fr(valeur):
    """260 -> « 260 », 260.5 -> « 260,5 » (messages destinés à l'affichage)."""
    texte = format(Decimal(valeur).normalize(), "f")
    return texte.replace(".", ",")


def alertes_suivi(*, actif, prochaine_pesee, seuil, seuil_g=POIDS_VENTE_G):
    """Alertes du suivi, calculées à la volée, seulement pour un lot actif."""
    if not actif:
        return []
    alertes = []
    if prochaine_pesee is not None and prochaine_pesee["en_retard"]:
        date_prevue = prochaine_pesee["date_prevue"]
        alertes.append({
            "code": "PESEE_EN_RETARD",
            "niveau": "attention",
            "message": (
                f"La pesée prévue le {date_prevue.isoformat()} n'a pas été saisie."
            ),
            "date_prevue": date_prevue,
            "jours_de_retard": prochaine_pesee["jours_de_retard"],
        })
    pesee = seuil["derniere_pesee_au_seuil"]
    if pesee is not None:
        alertes.append({
            "code": "POIDS_VENTE_ATTEINT",
            "niveau": "info",
            "message": (
                f"Le poids moyen a atteint {_nombre_fr(pesee.poids_moyen)} g, "
                f"au-delà du seuil de {seuil_g} g : fin de cycle possible."
            ),
            "poids_moyen": pesee.poids_moyen,
            "seuil_g": seuil_g,
            "date_pesee": pesee.date,
        })
    return alertes


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
