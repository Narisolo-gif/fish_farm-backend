"""Chaîne de démarrage : seul le pilotage (Administrateur, Directeur) crée et modifie."""
from django.test import TestCase

from apps.bassins.models import Bassin
from apps.core.roles import DIRECTEUR, EMPLOYE, GESTIONNAIRE

from .outils_droits import ClientsParRole


URL_REPRODUCTIONS = "/api/v1/reproduction/reproductions/"
URL_PONTES = "/api/v1/reproduction/pontes/"
URL_ECLOSIONS = "/api/v1/ecloserie/"
URL_LOTS = "/api/v1/lots/"


class DroitsChaineTests(ClientsParRole, TestCase):
    def setUp(self):
        self.bassin = Bassin.objects.create(
            reference="CHAINE-REP",
            categorie=Bassin.Categorie.REPRODUCTION,
            capacity=10000,
            statut=Bassin.Statut.ACTIF,
        )

    def _reproduction(self, client, reference="REPRO-1"):
        return client.post(
            URL_REPRODUCTIONS,
            {
                "reference": reference,
                "bassin": self.bassin.pk,
                "espece": "CARPE",
                "date_ajout": "2026-01-01",
                "quantite_male": 5,
                "quantite_femelle": 5,
            },
            format="json",
        )

    def _chaine(self, client):
        reproduction = self._reproduction(client)
        self.assertEqual(reproduction.status_code, 201, reproduction.data)
        ponte = client.post(
            URL_PONTES,
            {
                "reference": "PONTE-1",
                "bassin": self.bassin.pk,
                "date_ponte": "2026-01-02",
                "quantite_oeuf_obtenu": "5000.00",
            },
            format="json",
        )
        self.assertEqual(ponte.status_code, 201, ponte.data)
        eclosion = client.post(
            URL_ECLOSIONS,
            {
                "reference": "ECLOSION-1",
                "reference_ponte": "PONTE-1",
                "date_eclosion": "2026-01-03",
                "quantite_oeufs_eclos": "5000.00",
                "quantite_alevins_obtenu": 1000,
            },
            format="json",
        )
        self.assertEqual(eclosion.status_code, 201, eclosion.data)
        lot = client.post(
            URL_LOTS,
            {
                "reference": "LOT-1",
                "id_eclosion": eclosion.data["id"],
                "date_creation": "2026-01-04T12:00:00Z",
                "parcours_production": "SANS_TRAITEMENT",
            },
            format="json",
        )
        self.assertEqual(lot.status_code, 201, lot.data)
        return reproduction.data["id"], eclosion.data["id"]

    def test_directeur_cree_reproduction_ponte_eclosion_lot(self):
        self._chaine(self.client_de(DIRECTEUR))

    def test_gerante_et_employe_lisent_la_chaine_sans_ecrire(self):
        self._chaine(self.client_de(DIRECTEUR))
        for libelle in (GESTIONNAIRE, EMPLOYE):
            with self.subTest(role=libelle):
                client = self.client_de(libelle)
                for url in (URL_REPRODUCTIONS, URL_PONTES, URL_ECLOSIONS, URL_LOTS):
                    self.assertEqual(client.get(url).status_code, 200, url)
                self.assertEqual(self._reproduction(client, "REPRO-X").status_code, 403)
                eclosion = client.post(
                    URL_ECLOSIONS,
                    {
                        "reference": "ECLOSION-X",
                        "reference_ponte": "PONTE-1",
                        "date_eclosion": "2026-01-03",
                        "quantite_oeufs_eclos": "100.00",
                        "quantite_alevins_obtenu": 10,
                    },
                    format="json",
                )
                self.assertEqual(eclosion.status_code, 403)

    def test_desactivation_reservee_au_pilotage(self):
        id_reproduction, _ = self._chaine(self.client_de(DIRECTEUR))
        url = f"{URL_REPRODUCTIONS}{id_reproduction}/desactiver/"
        self.assertEqual(self.client_de(GESTIONNAIRE).patch(url).status_code, 403)
        reponse = self.client_de(DIRECTEUR).patch(url)
        self.assertNotEqual(reponse.status_code, 403, reponse.data)
