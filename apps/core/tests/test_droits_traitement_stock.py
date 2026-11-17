"""Traitement et mise en stock : ouverture, clôture et mise en stock par le pilotage."""
from django.test import TestCase
from django.utils import timezone

from apps.bassins.models import Bassin, Hapa
from apps.core.roles import ADMINISTRATEUR, DIRECTEUR, EMPLOYE, GESTIONNAIRE
from apps.provende.models import Provende, TypeProvende

from .outils_droits import ClientsParRole, creer_lot_par_api


URL_TRAITEMENT = "/api/v1/traitement/"
URL_STOCKS = "/api/v1/stocks/"


class DroitsTraitementStockTests(ClientsParRole, TestCase):
    def setUp(self):
        self.directeur = self.client_de(DIRECTEUR)

    def _bassin_avec_hapa(self, reference, categorie):
        bassin = Bassin.objects.create(
            reference=reference, categorie=categorie, capacity=10000, statut=Bassin.Statut.ACTIF
        )
        hapa = Hapa.objects.create(bassin=bassin, nom=f"H-{reference}", actif=True, statut=Hapa.Statut.ACTIF)
        return bassin, hapa

    def _donnees_traitement(self, id_lot):
        bassin, hapa = self._bassin_avec_hapa(f"TRT-{id_lot}", Bassin.Categorie.TRAITEMENT)
        type_provende, _ = TypeProvende.objects.get_or_create(type=TypeProvende.Type.TRAITEMENT)
        provende = Provende.objects.create(
            type_provende=type_provende, reference=f"PROV-{id_lot}", date_creation=timezone.localdate()
        )
        return {
            "id_lot": id_lot,
            "id_bassin": bassin.pk,
            "id_used_provende": provende.pk,
            "date_ouverture": timezone.localdate().isoformat(),
            "effectif_initial": 100,
            "hapas": [{"id_hapa": hapa.pk, "effectif_initial": 100}],
        }

    def _donnees_stock(self, id_lot):
        bassin, hapa = self._bassin_avec_hapa(f"STK-{id_lot}", Bassin.Categorie.STOCKS)
        return {
            "id_lot": id_lot,
            "id_bassin": bassin.pk,
            "finalite": "GROSSISSEMENT",
            "hapas": [{"id_hapa": hapa.pk, "effectif_entrant": 1000}],
        }

    def test_ouverture_et_cloture_reservees_au_directeur(self):
        id_lot = creer_lot_par_api(self, self.directeur, "T1", parcours="TRAITEMENT_REQUIS")
        donnees = self._donnees_traitement(id_lot)
        for libelle in (EMPLOYE, GESTIONNAIRE):
            with self.subTest(role=libelle):
                reponse = self.client_de(libelle).post(URL_TRAITEMENT, donnees, format="json")
                self.assertEqual(reponse.status_code, 403)
        cree = self.directeur.post(URL_TRAITEMENT, donnees, format="json")
        self.assertEqual(cree.status_code, 201, cree.data)
        url_cloture = f"{URL_TRAITEMENT}{cree.data['id']}/cloturer/"
        self.assertEqual(self.client_de(EMPLOYE).post(url_cloture).status_code, 403)
        cloture = self.directeur.post(url_cloture)
        self.assertEqual(cloture.status_code, 200, cloture.data)

    def test_tous_lisent_traitement_et_stock(self):
        id_lot = creer_lot_par_api(self, self.directeur, "S1")
        cree = self.directeur.post(URL_STOCKS, self._donnees_stock(id_lot), format="json")
        self.assertEqual(cree.status_code, 201, cree.data)
        for libelle in (EMPLOYE, GESTIONNAIRE):
            with self.subTest(role=libelle):
                client = self.client_de(libelle)
                self.assertEqual(client.get(URL_TRAITEMENT).status_code, 200)
                self.assertEqual(client.get(URL_STOCKS).status_code, 200)
                self.assertEqual(client.get(f"{URL_STOCKS}{cree.data['id']}/").status_code, 200)

    def test_mise_en_stock_par_le_directeur_suppression_par_l_admin(self):
        id_lot = creer_lot_par_api(self, self.directeur, "S2")
        donnees = self._donnees_stock(id_lot)
        self.assertEqual(
            self.client_de(EMPLOYE).post(URL_STOCKS, donnees, format="json").status_code, 403
        )
        cree = self.directeur.post(URL_STOCKS, donnees, format="json")
        self.assertEqual(cree.status_code, 201, cree.data)
        url = f"{URL_STOCKS}{cree.data['id']}/"
        self.assertEqual(self.directeur.delete(url).status_code, 403)
        self.assertEqual(self.client_de(ADMINISTRATEUR).delete(url).status_code, 204)
