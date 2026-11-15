"""Tests des calculs purs d'indicateurs : sans Django ni base de données.

Exécution : python -m unittest apps.grossissement.tests.test_calculs -v
"""
import unittest
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal as D

from apps.grossissement.calculs import (
    LotCalcul,
    PeseeCalcul,
    alertes_suivi,
    allouer_provende,
    biomasse_kg,
    calculer_instantanes,
    ecart_poids_pourcentage,
    echantillon_conforme,
    etat_calendrier,
    etat_seuil,
    gmq,
    ic,
    jour_echeance,
    poids_moyen_echantillon,
    pourcentage_reel_echantillon,
    repartir_provende,
    seuil_poids_vente,
    taille_echantillon_cible,
    taux_survie,
)


ENTREE = date(2026, 1, 1)


def _par_pesee(lot, quantites, lots, effectif_a):
    allocations = allouer_provende(quantites, lots, effectif_a)
    return {
        instantane["pesee_id"]: instantane
        for instantane in calculer_instantanes(lot, allocations[lot.id])
    }


class FonctionsElementairesTests(unittest.TestCase):
    def test_biomasse_arrondie_a_trois_decimales(self):
        self.assertEqual(biomasse_kg(980, D("45")), D("44.100"))
        self.assertEqual(biomasse_kg(3, D("0.15")), D("0.000"))  # 0,00045
        self.assertEqual(biomasse_kg(7, D("12.35")), D("0.086"))  # 0,08645

    def test_gmq(self):
        self.assertEqual(gmq(D("20"), D("45"), 30), D("0.83"))
        self.assertEqual(gmq(D("80"), D("60"), 18), D("-1.11"))

    def test_gmq_none_sans_jour(self):
        self.assertIsNone(gmq(D("20"), D("25"), 0))
        self.assertIsNone(gmq(D("20"), D("25"), -3))

    def test_ic(self):
        self.assertEqual(ic(D("50"), D("20"), D("44.1")), D("2.07"))
        self.assertEqual(ic(D("140"), D("20"), D("76")), D("2.50"))

    def test_ic_none_si_gain_negatif_ou_nul(self):
        self.assertIsNone(ic(D("10"), D("76"), D("57")))
        self.assertIsNone(ic(D("10"), D("76"), D("76")))

    def test_ic_none_si_provende_nulle(self):
        self.assertIsNone(ic(D("0"), D("20"), D("44.1")))

    def test_arrondi_demi_superieur(self):
        self.assertEqual(gmq(D("0"), D("0.125"), 1), D("0.13"))


class EchantillonTests(unittest.TestCase):
    """Pesée sur échantillon, dont la pesée initiale P0 de la sortie vers le grossissement."""

    def test_taille_echantillon_cible(self):
        self.assertEqual(taille_echantillon_cible(2000, 10), 200)
        self.assertEqual(taille_echantillon_cible(15, 10), 2)
        # Minimum 1 poisson.
        self.assertEqual(taille_echantillon_cible(5, 10), 1)

    def test_taille_echantillon_cible_pourcentage_decimal(self):
        self.assertEqual(taille_echantillon_cible(1000, D("12.5")), 125)
        self.assertEqual(taille_echantillon_cible(1001, D("12.5")), 126)

    def test_poids_moyen_echantillon(self):
        self.assertEqual(poids_moyen_echantillon(4000, 200), D("20.00"))
        self.assertEqual(poids_moyen_echantillon(1000, 3), D("333.33"))
        self.assertEqual(poids_moyen_echantillon(1001, 3), D("333.67"))

    def test_poids_moyen_arrondi_demi_superieur(self):
        # 2 537,50 / 100 = 25,375 -> 25,38 (demi supérieur, pas arrondi bancaire).
        self.assertEqual(poids_moyen_echantillon(D("2537.50"), 100), D("25.38"))

    def test_echantillon_conforme(self):
        self.assertTrue(echantillon_conforme(200, 200))
        self.assertFalse(echantillon_conforme(199, 200))
        self.assertTrue(echantillon_conforme(201, 200))

    def test_pourcentage_reel_echantillon(self):
        self.assertEqual(pourcentage_reel_echantillon(10, 100), D("10.00"))
        self.assertEqual(pourcentage_reel_echantillon(1, 3), D("33.33"))
        self.assertEqual(pourcentage_reel_echantillon(2, 3), D("66.67"))
        self.assertEqual(pourcentage_reel_echantillon(100, 100), D("100.00"))


class CalendrierTests(unittest.TestCase):
    """J30, J60 puis tous les 15 jours, sans limite ; information seulement."""

    def _etat(self, age, derniere_pesee_jour=None, tolerance=0):
        return etat_calendrier(
            ENTREE,
            ENTREE + timedelta(days=age),
            (
                ENTREE + timedelta(days=derniere_pesee_jour)
                if derniere_pesee_jour is not None
                else None
            ),
            tolerance_jours=tolerance,
        )

    def test_jours_des_echeances(self):
        self.assertEqual(
            [jour_echeance(rang) for rang in range(1, 7)],
            [30, 60, 75, 90, 105, 120],
        )
        self.assertEqual(jour_echeance(30), 60 + 28 * 15)

    def test_etape_courante(self):
        for age, etape in ((0, 1), (29, 1), (30, 2), (59, 2), (60, 3), (75, 4), (180, 11), (400, 25)):
            with self.subTest(age=age):
                self.assertEqual(self._etat(age)["etape_courante"], etape)

    def test_prochaine_pesee_suit_la_derniere_pesee_realisee(self):
        for age, derniere, numero, jour_cible in (
            # Sans pesée : J30, quel que soit l'âge du lot.
            (10, None, 1, 30),
            (70, None, 1, 30),
            # Pesée faite à l'échéance : l'échéance suivante.
            (35, 30, 2, 60),
            # Pesée faite avant l'échéance : celle-ci reste à faire.
            (35, 28, 1, 30),
            # J30 sautée, pesée rattrapée à J40 : prochaine J60, pas de faux retard.
            (45, 40, 2, 60),
            (200, 180, 11, 195),
        ):
            with self.subTest(age=age, derniere=derniere):
                prochaine = self._etat(age, derniere)["prochaine_pesee"]
                self.assertEqual(prochaine["numero"], numero)
                self.assertEqual(prochaine["jour_cible"], jour_cible)
                self.assertEqual(
                    prochaine["date_prevue"], ENTREE + timedelta(days=jour_cible)
                )

    def test_retard_jours_restants_et_tolerance(self):
        a_l_heure = self._etat(20)["prochaine_pesee"]
        self.assertEqual(
            (a_l_heure["en_retard"], a_l_heure["jours_restants"], a_l_heure["jours_de_retard"]),
            (False, 10, 0),
        )
        # Le jour même de l'échéance, la pesée n'est pas encore en retard.
        jour_meme = self._etat(30)["prochaine_pesee"]
        self.assertEqual((jour_meme["en_retard"], jour_meme["jours_restants"]), (False, 0))
        en_retard = self._etat(35)["prochaine_pesee"]
        self.assertEqual(
            (en_retard["en_retard"], en_retard["jours_restants"], en_retard["jours_de_retard"]),
            (True, 0, 5),
        )
        # Avec 5 jours de tolérance, J35 n'est pas encore en retard, J36 l'est.
        self.assertFalse(self._etat(35, tolerance=5)["prochaine_pesee"]["en_retard"])
        self.assertTrue(self._etat(36, tolerance=5)["prochaine_pesee"]["en_retard"])

    def test_pesees_prevues_jusqu_a_la_date_de_reference(self):
        self.assertEqual(self._etat(10)["pesees_prevues"], [])
        self.assertEqual(
            self._etat(68)["pesees_prevues"],
            [
                {"numero": 1, "jour_cible": 30, "date_prevue": date(2026, 1, 31)},
                {"numero": 2, "jour_cible": 60, "date_prevue": date(2026, 3, 2)},
            ],
        )


@dataclass(frozen=True)
class _Pesee:
    date: date
    poids_moyen: D


class SeuilEtAlertesTests(unittest.TestCase):
    def _prochaine(self, en_retard, jours=0):
        return {"date_prevue": date(2026, 1, 31), "en_retard": en_retard, "jours_de_retard": jours}

    def test_seuils_par_espece(self):
        self.assertEqual(seuil_poids_vente("TILAPIA"), 250)
        self.assertEqual(seuil_poids_vente("CARPE"), 300)

    def test_seuil_juge_sur_la_derniere_pesee(self):
        pesees = [
            _Pesee(date(2026, 3, 2), D("260")),
            _Pesee(date(2026, 1, 31), D("200")),
            _Pesee(date(2026, 3, 17), D("245")),
        ]
        etat = etat_seuil(pesees, 250)
        self.assertFalse(etat["atteint"])
        self.assertIsNone(etat["pesee_au_seuil"])
        # Repère historique conservé.
        self.assertEqual(etat["date_premiere_pesee_au_seuil"], date(2026, 3, 2))

        etat = etat_seuil([*pesees, _Pesee(date(2026, 4, 1), D("270"))], 250)
        self.assertTrue(etat["atteint"])
        self.assertEqual(etat["pesee_au_seuil"].date, date(2026, 4, 1))
        # Bornes : le seuil pile suffit, 1 centième en dessous non.
        self.assertTrue(etat_seuil([_Pesee(date(2026, 1, 31), D("300.00"))], 300)["atteint"])
        self.assertFalse(etat_seuil([_Pesee(date(2026, 1, 31), D("299.99"))], 300)["atteint"])
        vide = etat_seuil([], 250)
        self.assertEqual(
            (vide["atteint"], vide["date_premiere_pesee_au_seuil"]), (False, None)
        )

    def test_alertes_retard_et_poids_de_vente(self):
        seuil = etat_seuil([_Pesee(date(2026, 1, 31), D("260.00"))], 250)
        alertes = alertes_suivi(
            actif=True, prochaine_pesee=self._prochaine(True, 5), seuil=seuil, seuil_g=250
        )
        self.assertEqual([a["code"] for a in alertes], ["PESEE_EN_RETARD", "POIDS_VENTE_ATTEINT"])
        retard, vente = alertes
        self.assertEqual(retard["niveau"], "attention")
        self.assertEqual(retard["message"], "La pesée prévue le 2026-01-31 n'a pas été saisie.")
        self.assertEqual(retard["jours_de_retard"], 5)
        self.assertEqual(vente["niveau"], "info")
        self.assertEqual(
            vente["message"],
            "Le poids moyen a atteint 260 g, au-delà du seuil de 250 g : fin de cycle possible.",
        )
        self.assertEqual(vente["date_pesee"], date(2026, 1, 31))

    def test_aucune_alerte_pour_un_lot_cloture_ou_a_jour(self):
        seuil = etat_seuil([_Pesee(date(2026, 1, 31), D("260"))], 250)
        self.assertEqual(
            alertes_suivi(
                actif=False, prochaine_pesee=self._prochaine(True, 5), seuil=seuil, seuil_g=250
            ),
            [],
        )
        self.assertEqual(
            alertes_suivi(
                actif=True,
                prochaine_pesee=self._prochaine(False),
                seuil=etat_seuil([], 250),
                seuil_g=250,
            ),
            [],
        )


class ClotureCalculsTests(unittest.TestCase):
    def test_ecart_poids_pourcentage(self):
        self.assertEqual(ecart_poids_pourcentage(D("20"), D("260")), D("1200.00"))
        self.assertEqual(ecart_poids_pourcentage(D("20.00"), D("20.00")), D("0.00"))
        self.assertEqual(ecart_poids_pourcentage(D("3"), D("10")), D("233.33"))
        self.assertIsNone(ecart_poids_pourcentage(None, D("260")))
        self.assertIsNone(ecart_poids_pourcentage(D("0"), D("260")))

    def test_taux_survie(self):
        self.assertEqual(taux_survie(1000, 950), D("95.00"))
        self.assertEqual(taux_survie(3, 2), D("66.67"))
        self.assertEqual(taux_survie(1000, 0), D("0.00"))
        self.assertIsNone(taux_survie(0, 0))
        self.assertIsNone(taux_survie(1000, None))


class RepartitionTests(unittest.TestCase):
    def _verifier(self, quantite, biomasses, attendu):
        parts = repartir_provende(D(quantite), biomasses)
        self.assertEqual(parts, {k: D(v) for k, v in attendu.items()})
        self.assertEqual(sum(parts.values()), D(quantite))

    def test_biomasses_egales(self):
        # A 1000 × 50 g = 50 kg, B 500 × 100 g = 50 kg : A (plus petit id) prend le reste.
        self._verifier("10", {"A": D("50"), "B": D("50")}, {"A": "5.000", "B": "5.000"})

    def test_quarante_soixante(self):
        self._verifier("10", {"A": D("40"), "B": D("60")}, {"A": "4.000", "B": "6.000"})

    def test_biomasses_42_75_et_40(self):
        biomasses = {"A": D("42.75"), "B": D("40.00")}
        self._verifier("90", biomasses, {"A": "46.495", "B": "43.505"})
        self._verifier("30", biomasses, {"A": "15.498", "B": "14.502"})

    def test_biomasses_42_75_et_20(self):
        biomasses = {"A": D("42.75"), "B": D("20.00")}
        self._verifier("90", biomasses, {"A": "61.315", "B": "28.685"})
        self._verifier("30", biomasses, {"A": "20.438", "B": "9.562"})

    def test_un_seul_lot_recoit_tout(self):
        self._verifier("12.345", {"A": D("7")}, {"A": "12.345"})

    def test_somme_des_biomasses_nulle(self):
        self.assertEqual(repartir_provende(D("10"), {"A": D("0"), "B": D("0")}), {})
        self.assertEqual(repartir_provende(D("10"), {}), {})

    def test_egalite_plus_petit_identifiant_recoit_le_reste(self):
        parts = repartir_provende(D("10"), {2: D("1"), 1: D("1"), 3: D("1")})
        self.assertEqual(parts, {1: D("3.334"), 2: D("3.333"), 3: D("3.333")})

    def test_somme_toujours_exacte(self):
        for quantite in ("0.001", "1", "7.77", "99.999", "1234.56"):
            for biomasses in (
                {1: D("1"), 2: D("2"), 3: D("3")},
                {1: D("0.333"), 2: D("100")},
                {1: D("17.5"), 2: D("17.5")},
            ):
                parts = repartir_provende(D(quantite), biomasses)
                self.assertEqual(sum(parts.values()), D(quantite))


class LotSeulTests(unittest.TestCase):
    """Cas 1 : effectif 1000 à 20 g le 2026-01-01 (biomasse 20 kg)."""

    def setUp(self):
        self.lot = LotCalcul(
            id=1,
            date_entree=ENTREE,
            effectif_initial=1000,
            poids_initial=D("20"),
            pesees=[
                PeseeCalcul("p1", date(2026, 1, 31), 980, D("45")),
                PeseeCalcul("p2", date(2026, 3, 2), 950, D("80")),
            ],
        )
        self.quantites = {date(2026, 1, 15): D("50"), date(2026, 2, 15): D("90")}
        effectifs = {date(2026, 1, 15): 990, date(2026, 2, 15): 980}
        self.effectif_a = lambda lot_id, jour: effectifs[jour]

    def test_deux_pesees(self):
        resultats = _par_pesee(self.lot, self.quantites, [self.lot], self.effectif_a)
        p1, p2 = resultats["p1"], resultats["p2"]
        self.assertEqual(p1["jour_de_cycle"], 30)
        self.assertEqual(p1["biomasse_kg"], D("44.100"))
        self.assertEqual(p1["gmq_periode"], D("0.83"))
        self.assertEqual(p1["gmq_cumule"], D("0.83"))
        self.assertEqual(p1["provende_periode_kg"], D("50.000"))
        self.assertEqual(p1["ic_periode"], D("2.07"))
        self.assertEqual(p1["ic_cumule"], D("2.07"))

        self.assertEqual(p2["jour_de_cycle"], 60)
        self.assertEqual(p2["biomasse_kg"], D("76.000"))
        self.assertEqual(p2["gmq_periode"], D("1.17"))
        self.assertEqual(p2["gmq_cumule"], D("1.00"))
        self.assertEqual(p2["provende_periode_kg"], D("90.000"))
        self.assertEqual(p2["ic_periode"], D("2.82"))
        self.assertEqual(p2["ic_cumule"], D("2.50"))

    def test_gain_negatif_et_periode_sans_provende(self):
        self.lot.pesees += [
            PeseeCalcul("p3", date(2026, 3, 20), 950, D("60")),
            PeseeCalcul("p4", date(2026, 4, 1), 950, D("100")),
        ]
        self.quantites[date(2026, 3, 10)] = D("10")
        effectifs = {date(2026, 1, 15): 990, date(2026, 2, 15): 980, date(2026, 3, 10): 950}
        resultats = _par_pesee(
            self.lot, self.quantites, [self.lot], lambda lot_id, jour: effectifs[jour]
        )
        p3, p4 = resultats["p3"], resultats["p4"]
        self.assertEqual(p3["biomasse_kg"], D("57.000"))
        self.assertEqual(p3["provende_periode_kg"], D("10.000"))
        self.assertIsNone(p3["ic_periode"])
        self.assertEqual(p3["gmq_periode"], D("-1.11"))
        self.assertEqual(p3["ic_cumule"], D("4.05"))

        self.assertEqual(p4["provende_periode_kg"], D("0.000"))
        self.assertIsNone(p4["ic_periode"])
        self.assertEqual(p4["gmq_periode"], D("3.33"))
        self.assertEqual(p4["ic_cumule"], D("2.00"))
        self.assertEqual(p4["jour_de_cycle"], 90)

    def test_pesee_le_jour_de_l_entree(self):
        lot = LotCalcul(
            id=1,
            date_entree=ENTREE,
            effectif_initial=1000,
            poids_initial=D("20"),
            pesees=[PeseeCalcul("p0", ENTREE, 1000, D("20"))],
        )
        resultat = _par_pesee(lot, {}, [lot], lambda lot_id, jour: 1000)["p0"]
        self.assertEqual(resultat["jour_de_cycle"], 0)
        self.assertIsNone(resultat["gmq_periode"])
        self.assertIsNone(resultat["gmq_cumule"])
        self.assertEqual(resultat["provende_periode_kg"], D("0.000"))
        self.assertIsNone(resultat["ic_periode"])
        self.assertIsNone(resultat["ic_cumule"])

    def test_provende_avant_entree_ou_apres_cloture_non_allouee(self):
        self.lot.date_cloture = date(2026, 2, 10)
        quantites = {date(2025, 12, 31): D("5"), date(2026, 1, 15): D("50"), date(2026, 2, 15): D("90")}
        allocations = allouer_provende(quantites, [self.lot], lambda lot_id, jour: 990)
        self.assertEqual(allocations[1], {date(2026, 1, 15): D("50")})

    def test_lot_sans_poids_initial(self):
        self.lot.poids_initial = None
        resultats = _par_pesee(self.lot, self.quantites, [self.lot], self.effectif_a)
        p1, p2 = resultats["p1"], resultats["p2"]
        # Sans point 0 ni pesée antérieure, la provende de janvier n'est allouée à personne.
        self.assertEqual(p1["provende_periode_kg"], D("0.000"))
        for champ in ("gmq_periode", "gmq_cumule", "ic_periode", "ic_cumule"):
            self.assertIsNone(p1[champ], champ)
        self.assertEqual(p2["gmq_periode"], D("1.17"))
        self.assertEqual(p2["provende_periode_kg"], D("90.000"))
        self.assertEqual(p2["ic_periode"], D("2.82"))
        self.assertIsNone(p2["ic_cumule"])


class DeuxLotsTests(unittest.TestCase):
    """Scénario 2 : A (1000 à 20 g) et B (500 à 40 g), entrés le 2026-01-01."""

    def setUp(self):
        self.a = LotCalcul(id=1, date_entree=ENTREE, effectif_initial=1000, poids_initial=D("20"))
        self.b = LotCalcul(id=2, date_entree=ENTREE, effectif_initial=500, poids_initial=D("40"))
        self.quantites = {
            date(2026, 1, 10): D("40"),
            date(2026, 1, 20): D("60"),
            date(2026, 2, 10): D("90"),
            date(2026, 2, 20): D("30"),
        }

        def effectif_a(lot_id, jour):
            if lot_id == 1:
                return 950 if jour >= date(2026, 2, 5) else 1000
            return 500

        self.effectif_a = effectif_a

    def _instantanes(self):
        allocations = allouer_provende(self.quantites, [self.a, self.b], self.effectif_a)
        return {
            lot.id: {i["pesee_id"]: i for i in calculer_instantanes(lot, allocations[lot.id])}
            for lot in (self.a, self.b)
        }

    def test_recalcul_croise(self):
        # Étape 1 : pesée de A le 2026-01-31.
        self.a.pesees.append(PeseeCalcul("a1", date(2026, 1, 31), 1000, D("45")))
        a1 = self._instantanes()[1]["a1"]
        self.assertEqual(a1["jour_de_cycle"], 30)
        self.assertEqual(a1["biomasse_kg"], D("45.000"))
        self.assertEqual(a1["gmq_periode"], D("0.83"))
        self.assertEqual(a1["gmq_cumule"], D("0.83"))
        self.assertEqual(a1["provende_periode_kg"], D("50.000"))
        self.assertEqual(a1["ic_periode"], D("2.00"))
        self.assertEqual(a1["ic_cumule"], D("2.00"))

        # Étape 2 : pesée de A le 2026-03-02 ; B n'a pas de pesée (référence 20 kg).
        self.a.pesees.append(PeseeCalcul("a2", date(2026, 3, 2), 950, D("90")))
        a2 = self._instantanes()[1]["a2"]
        self.assertEqual(a2["biomasse_kg"], D("85.500"))
        self.assertEqual(a2["gmq_periode"], D("1.50"))
        self.assertEqual(a2["gmq_cumule"], D("1.17"))
        self.assertEqual(a2["provende_periode_kg"], D("81.753"))
        self.assertEqual(a2["ic_periode"], D("2.02"))

        # Étape 3 : pesée de B le 2026-01-31 ; la période 2 de A est recalculée.
        self.b.pesees.append(PeseeCalcul("b1", date(2026, 1, 31), 500, D("80")))
        instantanes = self._instantanes()
        b1, a2 = instantanes[2]["b1"], instantanes[1]["a2"]
        self.assertEqual(b1["biomasse_kg"], D("40.000"))
        self.assertEqual(b1["gmq_periode"], D("1.33"))
        self.assertEqual(b1["provende_periode_kg"], D("50.000"))
        self.assertEqual(b1["ic_periode"], D("2.50"))
        self.assertEqual(a2["provende_periode_kg"], D("61.993"))
        self.assertEqual(a2["ic_periode"], D("1.53"))
        self.assertEqual(a2["ic_cumule"], D("1.71"))

        # Étape 4 : pesée de B le 2026-03-02.
        self.b.pesees.append(PeseeCalcul("b2", date(2026, 3, 2), 500, D("140")))
        instantanes = self._instantanes()
        b2 = instantanes[2]["b2"]
        self.assertEqual(b2["biomasse_kg"], D("70.000"))
        self.assertEqual(b2["gmq_periode"], D("2.00"))
        self.assertEqual(b2["gmq_cumule"], D("1.67"))
        self.assertEqual(b2["provende_periode_kg"], D("58.007"))
        self.assertEqual(b2["ic_periode"], D("1.93"))
        self.assertEqual(b2["ic_cumule"], D("2.16"))

        # Étape 5 : la provende du bassin est entièrement répartie, sans doublon.
        a, b = instantanes[1], instantanes[2]
        self.assertEqual(
            a["a1"]["provende_periode_kg"] + b["b1"]["provende_periode_kg"], D("100.000")
        )
        self.assertEqual(
            a["a2"]["provende_periode_kg"] + b["b2"]["provende_periode_kg"], D("120.000")
        )


if __name__ == "__main__":
    unittest.main()
