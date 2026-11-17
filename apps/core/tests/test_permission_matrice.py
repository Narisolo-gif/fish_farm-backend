"""Classe PermissionMatrice : utilisateur actif, rôle, matrice et refus par défaut."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.core.models import Role
from apps.core.permissions import PermissionMatrice
from apps.core.roles import (
    ADMINISTRATEUR,
    DIRECTEUR,
    EMPLOYE,
    GESTIONNAIRE,
    ROLES,
)


User = get_user_model()


def vue(ressource, action):
    return type("VueFactice", (), {"ressource": ressource, "action": action})()


class PermissionMatriceTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.compteur = 0

    def _requete(self, user):
        requete = self.factory.get("/")
        requete.user = user
        return requete

    def _utilisateur(self, libelle, *, statut="ACTIF", is_active=True, sans_role=False):
        self.compteur += 1
        return User.objects.create_user(
            username=f"matrice-{self.compteur}",
            password="test-password",
            role=None if sans_role else Role.objects.get_or_create(libelle=libelle)[0],
            statut=statut,
            is_active=is_active,
        )

    def _autorise(self, user, ressource, action):
        return PermissionMatrice().has_permission(self._requete(user), vue(ressource, action))

    def test_refus_sans_connexion(self):
        self.assertFalse(self._autorise(AnonymousUser(), "traitement", "list"))

    def test_refus_compte_inactif_ou_sans_role(self):
        for user in (
            self._utilisateur(DIRECTEUR, statut="INACTIF"),
            self._utilisateur(DIRECTEUR, is_active=False),
            self._utilisateur(DIRECTEUR, sans_role=True),
        ):
            with self.subTest(user=user.username):
                self.assertFalse(self._autorise(user, "traitement", "list"))

    def test_action_autorisee_selon_le_role(self):
        attendus = {
            ADMINISTRATEUR: True,
            DIRECTEUR: True,
            GESTIONNAIRE: False,
            EMPLOYE: False,
        }
        for libelle, autorise in attendus.items():
            with self.subTest(role=libelle):
                user = self._utilisateur(libelle)
                self.assertEqual(self._autorise(user, "traitement", "cloturer"), autorise)
                self.assertTrue(self._autorise(user, "traitement", "list"))

    def test_refus_par_defaut(self):
        user = self._utilisateur(ADMINISTRATEUR)
        self.assertFalse(self._autorise(user, "ressource_inconnue", "list"))
        self.assertFalse(self._autorise(user, "traitement", "action_inexistante"))

    def test_methode_non_routee_laisse_passer(self):
        user = self._utilisateur(EMPLOYE)
        self.assertTrue(self._autorise(user, "pesees", None))

    def test_methode_exclue_du_viewset_laisse_passer(self):
        # Le routeur associe DELETE à `destroy` même si le viewset exclut `delete` :
        # DRF doit pouvoir répondre 405, pas 403.
        user = self._utilisateur(ADMINISTRATEUR)
        vue_pesees = type(
            "VueFactice",
            (),
            {"ressource": "pesees", "action": "destroy", "http_method_names": ["get", "post", "patch"]},
        )()
        requete = self.factory.delete("/")
        requete.user = user
        self.assertTrue(PermissionMatrice().has_permission(requete, vue_pesees))

    def test_options_metadata_pour_tout_utilisateur_actif(self):
        user = self._utilisateur(EMPLOYE)
        self.assertTrue(self._autorise(user, "traitement", "metadata"))
        self.assertFalse(self._autorise(AnonymousUser(), "traitement", "metadata"))

    def test_vue_sans_permission_propre_exige_un_utilisateur_actif(self):
        # Filet de sécurité : une vue qui ne déclare rien (ex. racine du routeur
        # DRF) hérite de DEFAULT_PERMISSION_CLASSES, pas de AllowAny.
        from rest_framework.test import APIClient

        self.assertEqual(APIClient().get("/api/v1/grossissement/").status_code, 401)
        client = APIClient()
        client.force_authenticate(self._utilisateur(EMPLOYE))
        self.assertEqual(client.get("/api/v1/grossissement/").status_code, 200)

    def test_connexion_jwt_reste_ouverte(self):
        from rest_framework.test import APIClient

        user = self._utilisateur(EMPLOYE)
        reponse = APIClient().post(
            "/api/auth/login/", {"username": user.username, "password": "test-password"}, format="json"
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("access", reponse.data)

    def test_roles_metier_crees(self):
        self.assertEqual(Role.objects.filter(libelle__in=ROLES).count(), 4)
