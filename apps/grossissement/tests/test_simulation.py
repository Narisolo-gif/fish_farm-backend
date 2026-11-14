"""Tests du générateur de données simulées : sans Django ni base de données.

Exécution : python -m unittest apps.grossissement.tests.test_simulation -v
"""
import unittest
from collections import Counter
from dataclasses import fields
from datetime import date, timedelta
from decimal import Decimal

from apps.grossissement.simulation import (
    CODES_SCENARIOS,
    DistributionSimulee,
    calendrier_pesees,
    construire_scenario,
    construire_scenarios,
)


REFERENCE = date(2026, 11, 14)


def _jours_manquants_max(dates_presentes, debut, fin):
    """Plus longue suite de jours sans donnée entre debut et fin inclus."""
    presentes = set(dates_presentes)
    plus_long = courant = 0
    jour = debut
    while jour <= fin:
        courant = 0 if jour in presentes else courant + 1
        plus_long = max(plus_long, courant)
        jour += timedelta(days=1)
    return plus_long


class ReproductibiliteTests(unittest.TestCase):
    def test_meme_graine_memes_donnees(self):
        for code in CODES_SCENARIOS:
            premier = construire_scenario(code, REFERENCE, seed=42)
            second = construire_scenario(code, REFERENCE, seed=42)
            self.assertEqual(premier, second, code)

    def test_graines_differentes_donnees_differentes(self):
        for code in CODES_SCENARIOS:
            self.assertNotEqual(
                construire_scenario(code, REFERENCE, seed=42),
                construire_scenario(code, REFERENCE, seed=7),
                code,
            )

    def test_selection_identique_a_l_execution_complete(self):
        complets = {s.code: s for s in construire_scenarios(CODES_SCENARIOS, REFERENCE)}
        self.assertEqual(construire_scenario("IC_ELEVE", REFERENCE), complets["IC_ELEVE"])

    def test_scenario_inconnu(self):
        with self.assertRaises(ValueError):
            construire_scenarios(["INEXISTANT"], REFERENCE)


class CalendrierTests(unittest.TestCase):
    def test_dix_pesees_pour_un_lot_entre_185_jours_avant(self):
        entree = REFERENCE - timedelta(days=185)
        dates = calendrier_pesees(entree, REFERENCE)
        self.assertEqual(
            [(d - entree).days for d in dates],
            [30, 60, 75, 90, 105, 120, 135, 150, 165, 180],
        )

    def test_calendrier_limite_a_la_date_de_reference(self):
        entree = REFERENCE - timedelta(days=80)
        self.assertEqual(
            [(d - entree).days for d in calendrier_pesees(entree, REFERENCE)],
            [30, 60, 75],
        )


class ScenariosTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = {s.code: s for s in construire_scenarios(CODES_SCENARIOS, REFERENCE)}

    def test_normal(self):
        scenario = self.scenarios["NORMAL"]
        (lot,) = scenario.lots
        self.assertEqual([p.jour_de_cycle for p in lot.pesees], [30, 60, 75, 90, 105, 120, 135, 150, 165, 180])
        poids = [p.poids_moyen_g for p in lot.pesees]
        self.assertTrue(Decimal("240") <= poids[-1] <= Decimal("260"), poids[-1])
        self.assertTrue(all(a < b for a, b in zip(poids, poids[1:])), poids)
        morts = sum(m.quantite for m in lot.mortalites)
        self.assertTrue(0.02 <= morts / 2000 <= 0.04, morts)

    def test_mortalite_anormale(self):
        (lot,) = self.scenarios["MORTALITE_ANORMALE"].lots
        par_jour = Counter()
        for mortalite in lot.mortalites:
            par_jour[mortalite.date] += mortalite.quantite
        meilleur = None
        for debut in sorted(par_jour):
            fenetre = sum(par_jour[debut + timedelta(days=i)] for i in range(5))
            vivants = lot.effectif_a(debut - timedelta(days=1))
            taux = fenetre / vivants
            if meilleur is None or taux > meilleur[1]:
                meilleur = (debut, taux)
        debut, taux = meilleur
        self.assertTrue(0.09 <= taux <= 0.11, taux)
        self.assertTrue(65 <= (debut - lot.date_entree).days <= 75, debut)

    def test_croissance_faible(self):
        (normal,) = [p for p in self.scenarios["NORMAL"].lots[0].pesees if p.jour_de_cycle == 90]
        (faible,) = [p for p in self.scenarios["CROISSANCE_FAIBLE"].lots[0].pesees if p.jour_de_cycle == 90]
        ecart = 1 - faible.poids_moyen_g / normal.poids_moyen_g
        self.assertTrue(Decimal("0.33") <= ecart <= Decimal("0.47"), ecart)

    def test_croissance_faible_provende_proportionnelle_au_gain(self):
        normal = self.scenarios["NORMAL"].statistiques()["provende_kg"]
        # Même période de 100 jours : comparaison avec IC_ELEVE ramené au niveau normal.
        eleve = self.scenarios["IC_ELEVE"].statistiques()["provende_kg"] / Decimal("1.6")
        faible = self.scenarios["CROISSANCE_FAIBLE"].statistiques()["provende_kg"]
        self.assertGreater(normal, 0)
        self.assertTrue(Decimal("0.4") <= faible / eleve <= Decimal("0.6"), faible / eleve)

    def test_ic_eleve(self):
        normal = self.scenarios["MORTALITE_ANORMALE"]  # même durée, sans facteur de provende
        eleve = self.scenarios["IC_ELEVE"]
        rapport = eleve.statistiques()["provende_kg"] / normal.statistiques()["provende_kg"]
        self.assertTrue(Decimal("1.5") <= rapport <= Decimal("1.8"), rapport)

    def test_pesee_en_retard(self):
        (lot,) = self.scenarios["PESEE_EN_RETARD"].lots
        self.assertEqual([p.jour_de_cycle for p in lot.pesees], [30, 60, 75])

    def test_parametre_hors_plage(self):
        parametres = self.scenarios["PARAMETRE_HORS_PLAGE"].parametres
        avant, fin = parametres[:-7], parametres[-7:]
        self.assertTrue(all(p.oxygene < Decimal("4") for p in fin))
        self.assertTrue(all(p.oxygene > Decimal("5") for p in avant))
        moyenne = lambda serie, champ: sum(getattr(p, champ) for p in serie) / len(serie)
        self.assertGreater(moyenne(fin, "temperature_eau") - moyenne(avant, "temperature_eau"), 4)

    def test_donnees_manquantes(self):
        scenario = self.scenarios["DONNEES_MANQUANTES"]
        (lot,) = scenario.lots
        self.assertEqual([p.jour_de_cycle for p in lot.pesees], [30])
        fin = REFERENCE - timedelta(days=1)
        jours_provende = {d.date_heure.date() for d in scenario.distributions}
        jours_parametres = {p.date_heure.date() for p in scenario.parametres}
        self.assertEqual(_jours_manquants_max(jours_provende, scenario.date_entree, fin), 10)
        self.assertEqual(_jours_manquants_max(jours_parametres, scenario.date_entree, fin), 15)

    def test_deux_lots(self):
        scenario = self.scenarios["DEUX_LOTS"]
        lot_a, lot_b = scenario.lots
        self.assertNotIn("lot", {f.name for f in fields(DistributionSimulee)})
        self.assertNotEqual(lot_a.profil.espece, lot_b.profil.espece)
        self.assertEqual(
            [p.date for p in lot_a.pesees], [p.date for p in lot_b.pesees]
        )
        self.assertNotEqual(lot_a.profil.effectif_initial, lot_b.profil.effectif_initial)
        self.assertNotEqual(
            [p.poids_moyen_g for p in lot_a.pesees], [p.poids_moyen_g for p in lot_b.pesees]
        )

    def test_coherence_de_tous_les_scenarios(self):
        for code, scenario in self.scenarios.items():
            with self.subTest(code=code):
                for lot in scenario.lots:
                    dates = (
                        [m.date for m in lot.mortalites]
                        + [p.date for p in lot.pesees]
                    )
                    self.assertTrue(all(lot.date_entree <= d <= REFERENCE for d in dates))
                    self.assertTrue(all(m.quantite > 0 for m in lot.mortalites))
                    restant = lot.profil.effectif_initial
                    for mortalite in lot.mortalites:
                        restant -= mortalite.quantite
                        self.assertGreater(restant, 0)
                    self.assertEqual(lot.pesees, sorted(lot.pesees, key=lambda p: p.date))
                    for pesee in lot.pesees:
                        cible = -(-pesee.effectif_vivant // 10)
                        self.assertEqual(pesee.nombre_poissons_peses, max(1, cible))
                        self.assertGreater(pesee.poids_total_g, 0)
                        self.assertEqual(pesee.poids_total_g, pesee.poids_total_g.quantize(Decimal("0.01")))
                par_jour = Counter(d.date_heure.date() for d in scenario.distributions)
                self.assertTrue(all(n <= 3 for n in par_jour.values()))
                self.assertTrue(all(d.quantite_kg > 0 for d in scenario.distributions))
                horodatages = [d.date_heure for d in scenario.distributions] + [
                    p.date_heure for p in scenario.parametres
                ]
                self.assertTrue(all(
                    scenario.date_entree <= h.date() < REFERENCE for h in horodatages
                ))
                self.assertEqual(len(set(d.date_heure for d in scenario.distributions)), len(scenario.distributions))


if __name__ == "__main__":
    unittest.main()
