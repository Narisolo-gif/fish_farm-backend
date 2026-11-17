"""Accès à l'API des paramètres environnementaux : utilisateur connecté et actif."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bassins.models import Bassin
from apps.core.models import Role
from apps.environnement.models import ParametreEnvironnemental


User = get_user_model()

URL = "/api/v1/environnement/"


class PermissionsEnvironnementTests(TestCase):
    def setUp(self):
        self.bassin = Bassin.objects.create(
            reference="ENV-BASSIN",
            categorie=Bassin.Categorie.GROSSISSEMENT,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )
        self.mesure = ParametreEnvironnemental.objects.create(
            bassin=self.bassin,
            date_heure_prelevement=timezone.now() - timedelta(hours=1),
            **self._valeurs(),
        )

    def _valeurs(self):
        return {
            "temperature_eau": "27.50",
            "temperature_air": "29.00",
            "oxygene": "6.10",
            "ph": "7.20",
            "nitrite": "0.05",
            "nitrate": "1.20",
            "ammoniac": "0.02",
        }

    def _client(self, libelle, *, statut="ACTIF"):
        self.compteur = getattr(self, "compteur", 0) + 1
        utilisateur = User.objects.create_user(
            username=f"env-{self.compteur}",
            password="test-password",
            role=Role.objects.get_or_create(libelle=libelle)[0],
            statut=statut,
        )
        client = APIClient()
        client.force_authenticate(utilisateur)
        return client

    def _routes_de_lecture(self):
        return (
            URL,
            f"{URL}{self.mesure.pk}/",
            f"{URL}bassin/{self.bassin.pk}/",
            f"{URL}bassin/{self.bassin.pk}/latest/",
            f"{URL}latest/",
        )

    def _corps_creation(self):
        return {
            "bassin": self.bassin.pk,
            "date_heure_prelevement": timezone.now().isoformat(),
            **self._valeurs(),
        }

    def test_sans_connexion_tout_est_refuse(self):
        client = APIClient()
        for url in self._routes_de_lecture():
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 401)
        self.assertEqual(
            client.post(URL, self._corps_creation(), format="json").status_code, 401
        )
        self.assertEqual(
            client.patch(f"{URL}{self.mesure.pk}/", {"ph": "7.00"}, format="json").status_code,
            401,
        )
        self.assertEqual(client.delete(f"{URL}{self.mesure.pk}/").status_code, 401)
        self.assertTrue(ParametreEnvironnemental.objects.filter(pk=self.mesure.pk).exists())
        self.assertEqual(ParametreEnvironnemental.objects.count(), 1)

    def test_compte_inactif_refuse(self):
        client = self._client("Gestionnaire", statut="INACTIF")
        self.assertEqual(client.get(URL).status_code, 403)
        self.assertEqual(
            client.post(URL, self._corps_creation(), format="json").status_code, 403
        )

    def test_tous_les_roles_lisent(self):
        for libelle in ("Administrateur", "Directeur", "Gestionnaire", "Employé"):
            with self.subTest(role=libelle):
                client = self._client(libelle)
                for url in self._routes_de_lecture():
                    self.assertEqual(client.get(url).status_code, 200, url)

    def test_saisie_par_le_terrain_correction_par_le_pilotage(self):
        # L'employé saisit ; la gérante lit seulement ; seul le pilotage corrige.
        for libelle in ("Administrateur", "Directeur", "Employé"):
            with self.subTest(role=libelle):
                creee = self._client(libelle).post(URL, self._corps_creation(), format="json")
                self.assertEqual(creee.status_code, 201, creee.data)
        self.assertEqual(
            self._client("Gestionnaire").post(URL, self._corps_creation(), format="json").status_code,
            403,
        )
        url = f"{URL}{self.mesure.pk}/"
        for libelle in ("Employé", "Gestionnaire"):
            with self.subTest(role=libelle):
                self.assertEqual(
                    self._client(libelle).patch(url, {"ph": "7.00"}, format="json").status_code, 403
                )
        self.assertEqual(
            self._client("Directeur").patch(url, {"ph": "7.00"}, format="json").status_code, 200
        )

    def test_suppression_reservee_au_pilotage(self):
        for libelle in ("Gestionnaire", "Employé"):
            with self.subTest(role=libelle):
                reponse = self._client(libelle).delete(f"{URL}{self.mesure.pk}/")
                self.assertEqual(reponse.status_code, 403)
        self.assertTrue(ParametreEnvironnemental.objects.filter(pk=self.mesure.pk).exists())

        reponse = self._client("Directeur").delete(f"{URL}{self.mesure.pk}/")
        self.assertEqual(reponse.status_code, 204)
        self.assertFalse(ParametreEnvironnemental.objects.filter(pk=self.mesure.pk).exists())
