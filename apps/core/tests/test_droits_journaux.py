"""Journaux de terrain : l'employé saisit, le pilotage corrige, la gérante lit."""
from django.test import TestCase
from django.utils import timezone

from apps.bassins.models import Bassin, Hapa
from apps.core.roles import DIRECTEUR, EMPLOYE, GESTIONNAIRE
from apps.effectifs.models import CauseMortalite
from apps.provende.models import Provende, TypeProvende

from .outils_droits import ClientsParRole, creer_lot_par_api


URL_EVENEMENTS = "/api/v1/effectifs/evenements-mortalite-perte/"
URL_DISTRIBUTIONS = "/api/v1/provende/consommations-provende/"
URL_ENVIRONNEMENT = "/api/v1/environnement/"


class DroitsJournauxTests(ClientsParRole, TestCase):
    def setUp(self):
        self.directeur = self.client_de(DIRECTEUR)
        id_lot = creer_lot_par_api(self, self.directeur, "J1")
        self.bassin = Bassin.objects.create(
            reference="J-STK", categorie=Bassin.Categorie.STOCKS, capacity=10000,
            statut=Bassin.Statut.ACTIF,
        )
        self.hapa = Hapa.objects.create(bassin=self.bassin, nom="J-H", actif=True, statut=Hapa.Statut.ACTIF)
        stock = self.directeur.post("/api/v1/stocks/", {
            "id_lot": id_lot, "id_bassin": self.bassin.pk, "finalite": "GROSSISSEMENT",
            "hapas": [{"id_hapa": self.hapa.pk, "effectif_entrant": 1000}],
        }, format="json")
        self.assertEqual(stock.status_code, 201, stock.data)
        type_provende, _ = TypeProvende.objects.get_or_create(type=TypeProvende.Type.CROISSANCE)
        self.provende = Provende.objects.create(
            type_provende=type_provende, reference="J-PROV",
            date_creation=timezone.localdate() - timezone.timedelta(days=30),
        )
        self.cause = CauseMortalite.objects.get(libelle="Inconnue")
        self.minute = 0

    def _mortalite(self, client):
        return client.post(URL_EVENEMENTS, {
            "type_evenement": "MORTALITE", "id_bassin": self.bassin.pk, "id_hapa": self.hapa.pk,
            "espece": "CARPE", "quantite": 1, "id_cause": self.cause.pk,
        }, format="json")

    def _distribution(self, client):
        self.minute += 1
        quand = timezone.localtime().replace(hour=6, minute=self.minute, second=0, microsecond=0)
        return client.post(URL_DISTRIBUTIONS, {
            "id_bassin": self.bassin.pk, "id_hapa": self.hapa.pk, "id_provende": self.provende.pk,
            "date_distribution": quand.isoformat(), "quantite_kg": "1.00",
        }, format="json")

    def _mesure(self, client):
        return client.post(URL_ENVIRONNEMENT, {
            "bassin": self.bassin.pk, "date_heure_prelevement": timezone.now().isoformat(),
            "temperature_eau": "27.50", "temperature_air": "29.00", "oxygene": "6.10",
            "ph": "7.20", "nitrite": "0.05", "nitrate": "1.20", "ammoniac": "0.02",
        }, format="json")

    def test_employe_saisit_les_faits_de_terrain(self):
        employe = self.client_de(EMPLOYE)
        for saisie in (self._mortalite, self._distribution, self._mesure):
            with self.subTest(saisie=saisie.__name__):
                reponse = saisie(employe)
                self.assertEqual(reponse.status_code, 201, reponse.data)

    def test_gerante_ne_saisit_pas(self):
        gerante = self.client_de(GESTIONNAIRE)
        for saisie in (self._mortalite, self._distribution, self._mesure):
            with self.subTest(saisie=saisie.__name__):
                self.assertEqual(saisie(gerante).status_code, 403)
        self.assertEqual(gerante.get(URL_EVENEMENTS).status_code, 200)

    def test_corrections_reservees_au_pilotage(self):
        employe = self.client_de(EMPLOYE)
        mortalite = self._mortalite(employe).data["id"]
        distribution = self._distribution(employe).data["id"]
        for url in (
            f"{URL_EVENEMENTS}{mortalite}/annuler/",
            f"{URL_DISTRIBUTIONS}{distribution}/annuler/",
        ):
            with self.subTest(url=url):
                for libelle in (EMPLOYE, GESTIONNAIRE):
                    reponse = self.client_de(libelle).post(url, {"motif": "Erreur"}, format="json")
                    self.assertEqual(reponse.status_code, 403, libelle)
                reponse = self.directeur.post(url, {"motif": "Erreur"}, format="json")
                # L'annulation d'une mortalité répond 201, celle d'une distribution 200.
                self.assertIn(reponse.status_code, (200, 201), reponse.data)
                self.assertEqual(reponse.data["statut"], "ANNULEE")
