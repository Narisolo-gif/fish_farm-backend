"""Référentiels : le directeur (et l'administrateur) écrivent, les autres lisent."""
from django.test import TestCase
from django.utils import timezone

from apps.bassins.models import Bassin
from apps.core.roles import DIRECTEUR, EMPLOYE, GESTIONNAIRE
from apps.provende.models import TypeProvende

from .outils_droits import ClientsParRole


class DroitsReferentielsTests(ClientsParRole, TestCase):
    def setUp(self):
        self.type_provende, _ = TypeProvende.objects.get_or_create(
            type=TypeProvende.Type.CROISSANCE
        )
        self.compteur = 0

    def _ref(self, prefixe):
        self.compteur += 1
        return f"{prefixe}-{self.compteur}"

    def _creer_bassin(self, client, categorie=Bassin.Categorie.STOCKS):
        return client.post(
            "/api/v1/bassins/",
            {
                "reference": self._ref("REF-BASSIN"),
                "categorie": categorie,
                "capacity": 100,
                "statut": "ACTIF",
            },
            format="json",
        )

    def test_directeur_ecrit_les_referentiels(self):
        directeur = self.client_de(DIRECTEUR)
        bassin = self._creer_bassin(directeur)
        self.assertEqual(bassin.status_code, 201, bassin.data)
        hapa = directeur.post(
            "/api/v1/bassins/hapas/",
            {"bassin": bassin.data["id"], "nom": self._ref("H"), "actif": True},
            format="json",
        )
        self.assertEqual(hapa.status_code, 201, hapa.data)
        provende = directeur.post(
            "/api/v1/provende/",
            {
                "type_provende": self.type_provende.pk,
                "reference": self._ref("PROV"),
                "date_creation": timezone.localdate().isoformat(),
            },
            format="json",
        )
        self.assertEqual(provende.status_code, 201, provende.data)
        cause = directeur.post(
            "/api/v1/effectifs/causes-mortalite/",
            {"libelle": self._ref("Cause"), "actif": True},
            format="json",
        )
        self.assertEqual(cause.status_code, 201, cause.data)

    def test_gerante_et_employe_lisent_seulement(self):
        for libelle in (GESTIONNAIRE, EMPLOYE):
            with self.subTest(role=libelle):
                client = self.client_de(libelle)
                for url in (
                    "/api/v1/bassins/",
                    "/api/v1/bassins/hapas/",
                    "/api/v1/provende/",
                    "/api/v1/provende/types/",
                    "/api/v1/effectifs/causes-mortalite/",
                    "/api/v1/effectifs/categories-perte/",
                ):
                    self.assertEqual(client.get(url).status_code, 200, url)
                self.assertEqual(self._creer_bassin(client).status_code, 403)
                self.assertEqual(
                    client.post(
                        "/api/v1/effectifs/causes-mortalite/",
                        {"libelle": self._ref("Cause"), "actif": True},
                        format="json",
                    ).status_code,
                    403,
                )
