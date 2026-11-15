"""Suivi de cycle d'un lot : contrat de la page de suivi (lecture seule).

Le scénario reprend le lot S1 du dossier Postman 07 : entrée le 2026-01-01
avec 1 000 poissons de 20 g, mortalités de 20 puis 30 poissons, 50 puis 90 kg
de provende, pesées de 45 g (J30) et 80 g (J60).

Les exemples de `docs/contrats/` sont produits par ce test :
    GENERER_EXEMPLES=1 python manage.py test apps.grossissement.tests.test_suivi
Sans cette variable, le test vérifie qu'ils ont toujours la même structure
que la réponse de l'API.
"""
import json
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Role
from apps.effectifs.models import MouvementEffectif
from apps.effectifs.services import enregistrer_mouvement
from apps.environnement.models import ParametreEnvironnemental
from apps.grossissement.models import LotGrossissement
from apps.grossissement.services import (
    cloturer_lot_grossissement,
    creer_pesee,
)
from apps.lots.models import Lot
from apps.provende.models import ConsommationProvende, Provende, TypeProvende

from .test_lot_grossissement import URL_LOTS, OutilsLotGrossissement


User = get_user_model()

ENTREE = date(2026, 1, 1)
DOSSIER_EXEMPLES = Path(settings.BASE_DIR) / "docs" / "contrats"
CLES_RACINE = {
    "lot",
    "nombre_pesees",
    "dernieres_valeurs",
    "point_initial",
    "series",
    "calendrier",
    "seuil",
    "alertes",
    "parametres_environnementaux",
    "autres_lots_du_bassin",
}


def _local(jour, heure=8):
    return timezone.make_aware(
        datetime.combine(jour, time(heure, 0)),
        timezone.get_current_timezone(),
    )


def _structure(valeur):
    """Squelette comparable d'une réponse : clés et types, valeurs ignorées."""
    if isinstance(valeur, dict):
        return {cle: _structure(element) for cle, element in valeur.items()}
    if isinstance(valeur, list):
        return [_structure(valeur[0])] if valeur else []
    if isinstance(valeur, bool):
        return "booléen"
    if isinstance(valeur, (int, float)):
        return "nombre"
    if valeur is None:
        return None
    return type(valeur).__name__


class SuiviLotTests(OutilsLotGrossissement, TestCase):
    def setUp(self):
        super().setUp()
        il_y_a = (self.today - ENTREE).days
        _, _, sortie_id, _ = self._sortie_validee("LOT-S1", 1000, il_y_a_jours=il_y_a)
        self.gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)

    # --- Construction du scénario ---------------------------------------

    def _mortalite(self, quantite, jour, espece=Lot.Espece.CARPE):
        enregistrer_mouvement(
            bassin=self.bassin_destination,
            espece=espece,
            type_mouvement=MouvementEffectif.TypeMouvement.MORTALITE,
            quantite=quantite,
            date_evenement=jour,
            saisi_par=self.user,
        )

    def _distribution(self, quantite_kg, jour):
        type_provende, _ = TypeProvende.objects.get_or_create(
            type=TypeProvende.Type.CROISSANCE
        )
        provende, _ = Provende.objects.get_or_create(
            reference="PROV-SUIVI",
            defaults={"type_provende": type_provende, "date_creation": ENTREE},
        )
        ConsommationProvende.objects.create(
            bassin=self.bassin_destination,
            provende=provende,
            date_distribution=_local(jour),
            quantite_kg=Decimal(quantite_kg),
            utilisateur=self.user,
        )

    def _peser(self, jour, nombre, poids_total, lot=None):
        return creer_pesee(
            lot_id=(lot or self.gross).pk,
            date_pesee=jour,
            nombre=nombre,
            poids_total=Decimal(poids_total),
            utilisateur=self.user,
        )

    def _scenario_s1(self):
        self._mortalite(20, date(2026, 1, 20))
        self._mortalite(30, date(2026, 2, 20))
        self._distribution("50", date(2026, 1, 15))
        self._distribution("90", date(2026, 2, 15))
        self._peser(date(2026, 1, 31), 98, "4410")
        self._peser(date(2026, 3, 2), 95, "7600")

    def _environnement(self, jour, heure=8, **valeurs):
        mesures = {
            "temperature_eau": "27.50",
            "temperature_air": "29.00",
            "oxygene": "6.10",
            "ph": "7.20",
            "nitrite": "0.05",
            "nitrate": "1.20",
            "ammoniac": "0.02",
            **valeurs,
        }
        return ParametreEnvironnemental.objects.create(
            bassin=self.bassin_destination,
            date_heure_prelevement=_local(jour, heure),
            utilisateur=self.user,
            **{champ: Decimal(valeur) for champ, valeur in mesures.items()},
        )

    def _suivi(self, date_reference=None, lot=None, client=None):
        url = f"{URL_LOTS}{(lot or self.gross).pk}/suivi/"
        params = {"date_reference": date_reference.isoformat()} if date_reference else {}
        return (client or self.client).get(url, params)

    def _ok(self, date_reference=None, lot=None):
        reponse = self._suivi(date_reference, lot)
        self.assertEqual(reponse.status_code, 200, reponse.data)
        return reponse.json()

    # --- Contrat --------------------------------------------------------

    def test_lot_avec_deux_pesees(self):
        self._scenario_s1()
        self._environnement(date(2026, 3, 9))
        data = self._ok(date(2026, 3, 10))

        self.assertEqual(set(data), CLES_RACINE)
        self.assertEqual(data["lot"], {
            "id": self.gross.pk,
            "code": self.gross.code,
            "espece": "CARPE",
            "id_bassin": self.bassin_destination.pk,
            "reference_bassin": "GROSS-DEST",
            "statut": "ACTIF",
            "date_entree": "2026-01-01",
            "jour_de_cycle": 68,
            "effectif_initial": 1000,
            "poids_moyen_initial": 20.0,
            "effectif_vivant_actuel": 950,
            "evacuation_requise": False,
        })
        self.assertEqual(data["nombre_pesees"], 2)
        premiere, seconde = data["series"]
        self.assertEqual(
            {cle: premiere[cle] for cle in premiere if cle != "pesee_id"},
            {
                "date": "2026-01-31",
                "jour_de_cycle": 30,
                "poids_moyen": 45.0,
                "gmq_periode": 0.83,
                "gmq_cumule": 0.83,
                "ic_periode": 2.07,
                "ic_cumule": 2.07,
                "biomasse_kg": 44.1,
                "effectif_vivant": 980,
                "provende_periode_kg": 50.0,
            },
        )
        self.assertEqual(
            {cle: seconde[cle] for cle in seconde if cle != "pesee_id"},
            {
                "date": "2026-03-02",
                "jour_de_cycle": 60,
                "poids_moyen": 80.0,
                "gmq_periode": 1.17,
                "gmq_cumule": 1.0,
                "ic_periode": 2.82,
                "ic_cumule": 2.5,
                "biomasse_kg": 76.0,
                "effectif_vivant": 950,
                "provende_periode_kg": 90.0,
            },
        )
        self.assertEqual(data["dernieres_valeurs"], {
            "date_pesee": "2026-03-02",
            "jour_de_cycle": 60,
            "effectif_vivant": 950,
            "poids_moyen": 80.0,
            "biomasse_kg": 76.0,
            "gmq_periode": 1.17,
            "gmq_cumule": 1.0,
            "ic_periode": 2.82,
            "ic_cumule": 2.5,
            "provende_periode_kg": 90.0,
            "nature": {"poids_moyen": "mesuré sur échantillon", "biomasse_kg": "estimée"},
        })
        self.assertEqual(data["point_initial"], {
            "date": "2026-01-01", "jour_de_cycle": 0, "poids_moyen": 20.0, "biomasse_kg": 20.0,
        })
        self.assertEqual(data["calendrier"], {
            "prochaine_pesee": {
                "numero": 3,
                "jour_cible": 75,
                "date_prevue": "2026-03-17",
                "jours_restants": 7,
                "en_retard": False,
                "jours_de_retard": 0,
            },
            "pesees_prevues": [
                {"numero": 1, "jour_cible": 30, "date_prevue": "2026-01-31"},
                {"numero": 2, "jour_cible": 60, "date_prevue": "2026-03-02"},
            ],
        })
        self.assertEqual(data["seuil"], {
            "poids_vente_g": 300,
            "atteint": False,
            "fin_cycle_possible": False,
            "date_premiere_pesee_au_seuil": None,
        })
        self.assertEqual(data["alertes"], [])
        self.assertEqual(data["parametres_environnementaux"], {
            "date_heure_prelevement": "2026-03-09T08:00:00+03:00",
            "temperature_eau": 27.5,
            "temperature_air": 29.0,
            "oxygene": 6.1,
            "ph": 7.2,
            "nitrite": 0.05,
            "nitrate": 1.2,
            "ammoniac": 0.02,
        })
        self.assertEqual(data["autres_lots_du_bassin"], [])

    def test_lot_sans_pesee(self):
        data = self._ok(date(2026, 1, 11))
        self.assertEqual(set(data), CLES_RACINE)
        self.assertEqual(data["nombre_pesees"], 0)
        self.assertIsNone(data["dernieres_valeurs"])
        self.assertEqual(data["series"], [])
        self.assertEqual(data["lot"]["jour_de_cycle"], 10)
        self.assertEqual(data["point_initial"]["biomasse_kg"], 20.0)
        self.assertEqual(data["calendrier"]["prochaine_pesee"], {
            "numero": 1,
            "jour_cible": 30,
            "date_prevue": "2026-01-31",
            "jours_restants": 20,
            "en_retard": False,
            "jours_de_retard": 0,
        })
        self.assertEqual(data["calendrier"]["pesees_prevues"], [])
        self.assertEqual(data["alertes"], [])
        self.assertIsNone(data["parametres_environnementaux"])

    def test_date_reference_limite_les_donnees_vues(self):
        self._scenario_s1()
        self._environnement(date(2026, 1, 25), temperature_eau="25.00")
        self._environnement(date(2026, 3, 9))
        data = self._ok(date(2026, 2, 10))
        self.assertEqual(data["nombre_pesees"], 1)
        self.assertEqual(data["dernieres_valeurs"]["date_pesee"], "2026-01-31")
        self.assertEqual(data["lot"]["effectif_vivant_actuel"], 980)
        self.assertEqual(data["parametres_environnementaux"]["temperature_eau"], 25.0)
        self.assertEqual(data["calendrier"]["prochaine_pesee"]["date_prevue"], "2026-03-02")

    def test_date_reference_par_defaut_aujourd_hui(self):
        self._scenario_s1()
        data = self._ok()
        self.assertEqual(data["lot"]["jour_de_cycle"], (self.today - ENTREE).days)
        self.assertEqual(data["nombre_pesees"], 2)

    def test_pesee_en_retard_puis_rattrapee(self):
        self._scenario_s1()
        data = self._ok(date(2026, 3, 25))
        prochaine = data["calendrier"]["prochaine_pesee"]
        self.assertEqual((prochaine["en_retard"], prochaine["jours_de_retard"]), (True, 8))
        self.assertEqual(data["alertes"], [{
            "code": "PESEE_EN_RETARD",
            "niveau": "attention",
            "message": "La pesée prévue le 2026-03-17 n'a pas été saisie.",
            "date_prevue": "2026-03-17",
            "jours_de_retard": 8,
        }])
        # Échéance J75 sautée, pesée rattrapée le 2026-03-24 : prochaine J90, plus de retard.
        self._peser(date(2026, 3, 24), 95, "8550")
        data = self._ok(date(2026, 3, 25))
        self.assertEqual(data["calendrier"]["prochaine_pesee"]["jour_cible"], 90)
        self.assertEqual(data["alertes"], [])

    def test_seuil_de_vente_juge_sur_la_derniere_pesee(self):
        # CARPE : seuil de 300 g.
        self._scenario_s1()
        self._peser(date(2026, 3, 17), 95, "29450")  # 310 g
        self._peser(date(2026, 4, 1), 95, "28025")   # 295 g : retombé sous le seuil
        data = self._ok(date(2026, 4, 2))
        self.assertEqual(data["seuil"], {
            "poids_vente_g": 300,
            "atteint": False,
            "fin_cycle_possible": False,
            "date_premiere_pesee_au_seuil": "2026-03-17",
        })
        self.assertEqual(data["alertes"], [])

        self._peser(date(2026, 4, 10), 95, "30400")  # 320 g
        data = self._ok(date(2026, 4, 11))
        self.assertEqual(data["seuil"], {
            "poids_vente_g": 300,
            "atteint": True,
            "fin_cycle_possible": True,
            "date_premiere_pesee_au_seuil": "2026-03-17",
        })
        self.assertEqual(data["alertes"], [{
            "code": "POIDS_VENTE_ATTEINT",
            "niveau": "info",
            "message": (
                "Le poids moyen a atteint 320 g, au-delà du seuil de 300 g : "
                "fin de cycle possible."
            ),
            "poids_moyen": 320.0,
            "seuil_g": 300,
            "date_pesee": "2026-04-10",
        }])

    def test_lot_cloture_sans_alerte_ni_prochaine_pesee(self):
        self._scenario_s1()
        self._peser(date(2026, 3, 17), 95, "28500")  # 300 g
        cloturer_lot_grossissement(self.gross.pk, utilisateur=self.user)
        # Vu après la clôture (du jour) : le cycle est figé à la date de clôture.
        data = self._ok(self.today + timedelta(days=30))
        self.assertEqual(data["lot"]["statut"], "CLOTURE")
        self.assertEqual(data["lot"]["jour_de_cycle"], (self.today - ENTREE).days)
        self.assertTrue(data["lot"]["evacuation_requise"])
        self.assertIsNone(data["calendrier"]["prochaine_pesee"])
        self.assertEqual(data["alertes"], [])
        self.assertTrue(data["seuil"]["atteint"])
        self.assertFalse(data["seuil"]["fin_cycle_possible"])
        self.assertEqual(data["nombre_pesees"], 3)

    def test_valeurs_non_calculables_a_null(self):
        # Sans provende, l'IC n'est pas calculable ; sans P0, ni le point initial
        # ni les indicateurs cumulés.
        self._peser(date(2026, 1, 31), 100, "4500")
        data = self._ok(date(2026, 2, 1))
        self.assertIsNone(data["dernieres_valeurs"]["ic_periode"])
        self.assertIsNone(data["dernieres_valeurs"]["ic_cumule"])
        self.assertEqual(data["dernieres_valeurs"]["provende_periode_kg"], 0.0)

        LotGrossissement.objects.filter(pk=self.gross.pk).update(poids_moyen_initial=None)
        data = self._ok(date(2026, 2, 1))
        self.assertIsNone(data["lot"]["poids_moyen_initial"])
        self.assertEqual(data["point_initial"]["poids_moyen"], None)
        self.assertEqual(data["point_initial"]["biomasse_kg"], None)

    def test_autre_lot_du_bassin(self):
        il_y_a = (self.today - ENTREE).days
        lot_t, _ = self._mettre_en_stock(
            "LOT-T",
            200,
            il_y_a_jours=il_y_a + 1,
            espece=Lot.Espece.TILAPIA,
            hapa=self._hapa(self.bassin_stock, "STOCK-T"),
        )
        validee = self._valider(self._donnees_sortie(lot_t, 200, il_y_a_jours=il_y_a))
        self.assertEqual(validee.status_code, 201, validee.data)
        data = self._ok(date(2026, 1, 11))
        self.assertEqual(data["autres_lots_du_bassin"], [
            {"id": validee.data["id"], "code": validee.data["code"], "espece": "TILAPIA"},
        ])

    def test_erreurs_et_permissions(self):
        for valeur in ("2026-02-30", "10/03/2026", "abc"):
            with self.subTest(valeur=valeur):
                reponse = self.client.get(
                    f"{URL_LOTS}{self.gross.pk}/suivi/", {"date_reference": valeur}
                )
                self.assertEqual(reponse.status_code, 400, reponse.data)
                self.assertIn("date_reference", reponse.data)
        self.assertEqual(self.client.get(f"{URL_LOTS}999999/suivi/").status_code, 404)

        employe = User.objects.create_user(
            username="suivi-employe",
            password="test-password",
            role=Role.objects.create(libelle="Employé"),
            statut="ACTIF",
        )
        client = APIClient()
        client.force_authenticate(employe)
        self.assertEqual(self._suivi(client=client).status_code, 200)
        employe.statut = "INACTIF"
        employe.save(update_fields=["statut"])
        self.assertIn(self._suivi(client=client).status_code, (401, 403))
        self.assertEqual(self._suivi(client=APIClient()).status_code, 401)

    def test_liste_de_selection_des_lots_en_cours(self):
        reponse = self.client.get(URL_LOTS, {"statut": "ACTIF"})
        self.assertEqual(reponse.status_code, 200, reponse.data)
        (lot,) = reponse.json()
        self.assertEqual(lot["date_entree"], "2026-01-01")
        self.assertEqual(lot["jour_de_cycle"], (self.today - ENTREE).days)
        for champ in ("id", "code", "espece", "id_bassin", "reference_bassin", "statut"):
            self.assertIn(champ, lot)

    # --- Exemples du contrat (docs/contrats) ----------------------------

    def _exemple(self, nom, contenu):
        chemin = DOSSIER_EXEMPLES / nom
        if os.environ.get("GENERER_EXEMPLES") == "1":
            DOSSIER_EXEMPLES.mkdir(parents=True, exist_ok=True)
            chemin.write_text(
                json.dumps(contenu, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return
        self.assertTrue(chemin.exists(), f"Exemple absent : {chemin}")
        exemple = json.loads(chemin.read_text(encoding="utf-8"))
        self.assertEqual(_structure(exemple), _structure(contenu), nom)

    def test_exemples_du_contrat_a_jour(self):
        self._scenario_s1()
        self._environnement(date(2026, 3, 9))
        self._exemple("suivi_lot.exemple.json", self._ok(date(2026, 3, 10)))

        vide = self._mettre_en_stock(
            "LOT-VIDE",
            500,
            il_y_a_jours=(self.today - ENTREE).days + 1,
            espece=Lot.Espece.TILAPIA,
            hapa=self._hapa(self.bassin_stock, "STOCK-V"),
        )[0]
        destination = self._bassin("GROSS-DEST-V", "GROSSISSEMENT")
        validee = self._valider(self._donnees_sortie(
            vide, 500, il_y_a_jours=(self.today - ENTREE).days, destination=destination,
            poids_moyen=Decimal("40"),
        ))
        self.assertEqual(validee.status_code, 201, validee.data)
        lot_vide = LotGrossissement.objects.get(pk=validee.data["id"])
        self._exemple("suivi_lot_vide.exemple.json", self._ok(date(2026, 1, 11), lot=lot_vide))

        liste = self.client.get(URL_LOTS, {"statut": "ACTIF"}).json()
        self._exemple("lots_en_cours.exemple.json", liste)
