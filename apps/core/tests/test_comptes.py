"""Comptes utilisateurs : matrice des droits et garde-fou du rôle Administrateur."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Role
from apps.core.roles import ADMINISTRATEUR, DIRECTEUR, EMPLOYE, GESTIONNAIRE


User = get_user_model()

URL_USERS = "/api/users/"


class ComptesTests(TestCase):
    def setUp(self):
        self.compteur = 0

    def _role(self, libelle):
        return Role.objects.get_or_create(libelle=libelle)[0]

    def _utilisateur(self, libelle, *, statut="ACTIF"):
        self.compteur += 1
        return User.objects.create_user(
            username=f"compte-{self.compteur}",
            password="ancien-mot-de-passe",
            role=self._role(libelle),
            statut=statut,
        )

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _creer(self, client, libelle):
        self.compteur += 1
        return client.post(
            URL_USERS,
            {"username": f"nouveau-{self.compteur}", "role_id": self._role(libelle).pk},
            format="json",
        )

    def test_gerante_gere_les_comptes_non_administrateurs(self):
        gerante = self._client(self._utilisateur(GESTIONNAIRE))
        cree = self._creer(gerante, EMPLOYE)
        self.assertEqual(cree.status_code, 201, cree.data)
        url = f"{URL_USERS}{cree.data['id']}/"
        promu = gerante.patch(url, {"role_id": self._role(DIRECTEUR).pk}, format="json")
        self.assertEqual(promu.status_code, 200, promu.data)
        desactive = gerante.patch(url, {"statut": "INACTIF"}, format="json")
        self.assertEqual(desactive.status_code, 200, desactive.data)
        self.assertEqual(gerante.post(f"{url}reset-password/").status_code, 200)

    def test_gerante_ne_touche_pas_au_role_administrateur(self):
        moi = self._utilisateur(GESTIONNAIRE)
        gerante = self._client(moi)
        admin = self._utilisateur(ADMINISTRATEUR)
        url_admin = f"{URL_USERS}{admin.pk}/"

        self.assertEqual(self._creer(gerante, ADMINISTRATEUR).status_code, 403)
        self.assertEqual(
            gerante.patch(url_admin, {"first_name": "X"}, format="json").status_code, 403
        )
        self.assertEqual(gerante.post(f"{url_admin}reset-password/").status_code, 403)
        self.assertEqual(gerante.delete(url_admin).status_code, 403)
        promotion = gerante.patch(
            f"{URL_USERS}{moi.pk}/",
            {"role_id": self._role(ADMINISTRATEUR).pk},
            format="json",
        )
        self.assertEqual(promotion.status_code, 403)
        moi.refresh_from_db()
        self.assertEqual(moi.role.libelle, GESTIONNAIRE)
        self.assertTrue(User.objects.filter(pk=admin.pk).exists())

    def test_gerante_ne_touche_pas_a_un_superutilisateur(self):
        # Un compte createsuperuser (accès /admin/) n'a pas forcément le rôle
        # Administrateur : il est protégé comme un compte Administrateur.
        gerante = self._client(self._utilisateur(GESTIONNAIRE))
        for options in ({"is_superuser": True, "is_staff": True}, {"is_staff": True}):
            with self.subTest(options=options):
                cible = self._utilisateur(EMPLOYE)
                for champ, valeur in options.items():
                    setattr(cible, champ, valeur)
                cible.save()
                url = f"{URL_USERS}{cible.pk}/"
                self.assertEqual(gerante.post(f"{url}reset-password/").status_code, 403)
                self.assertEqual(gerante.patch(url, {"first_name": "X"}, format="json").status_code, 403)
                self.assertEqual(gerante.delete(url).status_code, 403)

    def test_administrateur_attribue_le_role_administrateur(self):
        admin = self._client(self._utilisateur(ADMINISTRATEUR))
        self.assertEqual(self._creer(admin, ADMINISTRATEUR).status_code, 201)

    def test_directeur_lit_les_comptes_sans_les_modifier(self):
        directeur = self._client(self._utilisateur(DIRECTEUR))
        self.assertEqual(directeur.get(URL_USERS).status_code, 200)
        self.assertEqual(self._creer(directeur, EMPLOYE).status_code, 403)
        employe = self._client(self._utilisateur(EMPLOYE))
        self.assertEqual(employe.get(URL_USERS).status_code, 403)

    def test_roles_lisibles_par_tous(self):
        employe = self._client(self._utilisateur(EMPLOYE))
        self.assertEqual(employe.get("/api/roles/").status_code, 200)
        self.assertEqual(
            employe.post("/api/roles/", {"libelle": "Nouveau"}, format="json").status_code, 403
        )

    def test_me_et_change_password(self):
        employe = self._client(self._utilisateur(EMPLOYE))
        self.assertEqual(employe.get("/api/auth/me/").status_code, 200)
        change = employe.post(
            f"{URL_USERS}change-password/",
            {"ancien_mot_de_passe": "ancien-mot-de-passe", "nouveau_mot_de_passe": "nouveau-mdp-123"},
            format="json",
        )
        self.assertEqual(change.status_code, 200, change.data)
        inactif = self._client(self._utilisateur(EMPLOYE, statut="INACTIF"))
        self.assertEqual(inactif.get("/api/auth/me/").status_code, 403)
