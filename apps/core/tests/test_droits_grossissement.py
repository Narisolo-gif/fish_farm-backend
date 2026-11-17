"""Grossissement : sortie, clôture et corrections par le pilotage ; pesée par le terrain."""
from django.test import TestCase

from apps.core.roles import ADMINISTRATEUR, DIRECTEUR, EMPLOYE, GESTIONNAIRE
from apps.grossissement.models import LotGrossissement
from apps.grossissement.tests.test_lot_grossissement import URL_LOTS, OutilsLotGrossissement

from .outils_droits import ClientsParRole


URL_PESEES = "/api/v1/grossissement/pesees/"


class DroitsGrossissementTests(OutilsLotGrossissement, ClientsParRole, TestCase):
    def _pesee(self, client, gross):
        return client.post(URL_PESEES, {
            "id_lot_grossissement": gross.pk,
            "date": self.today.isoformat(),
            "nombre_poissons_peses": 10,
            "poids_total_g": "500.00",
        }, format="json")

    def test_sortie_cloture_evacuation_reservees_au_directeur(self):
        lot, _ = self._mettre_en_stock("LOT-DROITS-G", 100)
        donnees = self._donnees_sortie(lot, 100)
        self.assertEqual(
            self.client_de(GESTIONNAIRE).post(URL_LOTS, donnees, format="json").status_code, 403
        )
        self.assertEqual(
            self.client_de(EMPLOYE).post(f"{URL_LOTS}apercu-sortie/", donnees, format="json").status_code,
            403,
        )
        cree = self.client_de(DIRECTEUR).post(URL_LOTS, donnees, format="json")
        self.assertEqual(cree.status_code, 201, cree.data)
        for action in ("cloturer", "evacuer"):
            with self.subTest(action=action):
                reponse = self.client_de(GESTIONNAIRE).post(f"{URL_LOTS}{cree.data['id']}/{action}/", {}, format="json")
                self.assertEqual(reponse.status_code, 403)
        self.assertEqual(self.client_de(EMPLOYE).get(f"{URL_LOTS}{cree.data['id']}/suivi/").status_code, 200)

    def test_employe_pese_sans_corriger(self):
        _, _, sortie_id, _ = self._sortie_validee("LOT-DROITS-P", 100, il_y_a_jours=10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        employe = self.client_de(EMPLOYE)
        pesee = self._pesee(employe, gross)
        self.assertEqual(pesee.status_code, 201, pesee.data)
        url = f"{URL_PESEES}{pesee.data['id']}/"
        self.assertEqual(employe.patch(url, {"poids_total_g": "510.00"}, format="json").status_code, 403)
        self.assertEqual(employe.post(f"{url}annuler/", {"motif": "Erreur"}, format="json").status_code, 403)
        self.assertEqual(
            self.client_de(GESTIONNAIRE).patch(url, {"poids_total_g": "510.00"}, format="json").status_code,
            403,
        )
        corrigee = self.client_de(DIRECTEUR).patch(url, {"poids_total_g": "510.00"}, format="json")
        self.assertEqual(corrigee.status_code, 200, corrigee.data)

    def test_methodes_non_routees_toujours_405(self):
        _, _, sortie_id, _ = self._sortie_validee("LOT-DROITS-405", 100, il_y_a_jours=10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        pesee = self._pesee(self.client_de(DIRECTEUR), gross)
        admin = self.client_de(ADMINISTRATEUR)
        self.assertEqual(admin.delete(f"{URL_PESEES}{pesee.data['id']}/").status_code, 405)
        self.assertEqual(admin.delete(f"{URL_LOTS}{gross.pk}/").status_code, 405)
