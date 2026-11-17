"""Couverture de la matrice : chaque route en relève, et la matrice suit la conception.

`ATTENDU` est recopié de context/CONCEPTION_MATRICE_PERMISSIONS.md (section 4),
indépendamment de MATRICE : un écart entre les deux fait échouer le test.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIRequestFactory

from apps.core.models import Role
from apps.core.permissions import MATRICE, PermissionMatrice


User = get_user_model()

A, D, G, E = "Administrateur", "Directeur", "Gestionnaire", "Employé"
TOUS = {A, D, G, E}
PILOTAGE = {A, D}
TERRAIN = {A, D, E}
COMPTES = {A, G}
LECTURE = {"list": TOUS, "retrieve": TOUS}
CRUD = {**LECTURE, "create": PILOTAGE, "update": PILOTAGE, "partial_update": PILOTAGE, "destroy": PILOTAGE}

ATTENDU = {
    "roles": {**LECTURE, "create": {A}, "update": {A}, "partial_update": {A}, "destroy": {A}},
    "utilisateurs": {
        "list": {A, D, G}, "retrieve": {A, D, G},
        "create": COMPTES, "update": COMPTES, "partial_update": COMPTES, "destroy": COMPTES,
        "reset_password": COMPTES, "change_password": TOUS,
    },
    "bassins": CRUD, "hapas": CRUD, "types_provende": CRUD, "provendes": CRUD,
    "causes_mortalite": CRUD, "categories_perte": CRUD,
    "reproductions": {**CRUD, "desactiver": PILOTAGE},
    "pontes": {**CRUD, "desactiver": PILOTAGE},
    "eclosions": {**CRUD, "desactiver": PILOTAGE, "getbyref_pontes": TOUS, "getbydateEclosion": TOUS},
    "lots": CRUD,
    "traitement": {**CRUD, "effectifs": TOUS, "apercu_cloture": TOUS, "cloturer": PILOTAGE},
    "stocks": {**LECTURE, "create": PILOTAGE, "update": PILOTAGE, "partial_update": PILOTAGE, "destroy": {A}},
    "mouvements": {**LECTURE, "solde": TOUS, "par_hapa": TOUS, "annuler": PILOTAGE},
    "evenements_mortalite_perte": {**LECTURE, "totaux": TOUS, "create": TERRAIN, "annuler": PILOTAGE},
    "consommations_provende": {
        **LECTURE, "totaux_journaliers": TOUS, "jours_manquants_action": TOUS,
        "create": TERRAIN, "annuler": PILOTAGE,
    },
    "environnement": {
        **LECTURE, "historique_bassin": TOUS, "derniere_mesure_bassin": TOUS, "dernieres_mesures": TOUS,
        "create": TERRAIN, "update": PILOTAGE, "partial_update": PILOTAGE, "destroy": PILOTAGE,
    },
    "sorties_stock": {**LECTURE, "annuler": PILOTAGE},
    "lots_grossissement": {
        **LECTURE, "suivi": TOUS, "origine": TOUS, "apercu_cloture": TOUS, "historique_clotures": TOUS,
        "apercu_sortie": PILOTAGE, "create": PILOTAGE, "partial_update": PILOTAGE,
        "cloturer": PILOTAGE, "evacuer": PILOTAGE,
    },
    "pesees": {**LECTURE, "create": TERRAIN, "apercu": TERRAIN, "partial_update": PILOTAGE, "annuler": PILOTAGE},
}


def routes_viewsets(motifs=None, prefixe=""):
    """(chemin, classe du viewset, actions routées) pour chaque route DRF."""
    for motif in motifs if motifs is not None else get_resolver().url_patterns:
        if isinstance(motif, URLResolver):
            yield from routes_viewsets(motif.url_patterns, prefixe + str(motif.pattern))
        elif isinstance(motif, URLPattern):
            cls = getattr(motif.callback, "cls", None)
            actions = getattr(motif.callback, "actions", None)
            if cls is not None and actions:
                # Une méthode exclue de http_method_names répond 405 : hors matrice.
                routees = {
                    action for methode, action in actions.items()
                    if methode in cls.http_method_names
                }
                yield prefixe + str(motif.pattern), cls, routees


class CouvertureMatriceTests(TestCase):
    def test_chaque_route_est_couverte_par_la_matrice(self):
        manques = []
        vues = 0
        for chemin, cls, actions in routes_viewsets():
            if not chemin.startswith("api/"):
                continue
            vues += 1
            ressource = getattr(cls, "ressource", None)
            if ressource not in MATRICE:
                manques.append(f"{cls.__name__} ({chemin}) : ressource absente ({ressource!r})")
                continue
            if PermissionMatrice not in cls.permission_classes:
                manques.append(f"{cls.__name__} : PermissionMatrice absente")
            for action in actions:
                if action not in MATRICE[ressource]:
                    manques.append(f"{cls.__name__}.{action} ({chemin}) : action absente de la matrice")
                # Une action qui déclare ses propres permission_classes échappe à la matrice.
                methode = getattr(cls, action, None)
                if "permission_classes" in getattr(methode, "kwargs", {}):
                    manques.append(f"{cls.__name__}.{action} : permission_classes propre (hors matrice)")
        self.assertGreater(vues, 30)
        self.assertEqual(manques, [], "\n".join(manques))

    def test_matrice_conforme_a_la_conception(self):
        self.assertEqual(set(MATRICE), set(ATTENDU))
        for ressource, actions in ATTENDU.items():
            with self.subTest(ressource=ressource):
                self.assertEqual(
                    {action: set(roles) for action, roles in MATRICE[ressource].items()},
                    actions,
                )

    def test_chaque_role_sur_chaque_action(self):
        factory = APIRequestFactory()
        utilisateurs = {}
        for i, libelle in enumerate((A, D, G, E)):
            role = Role.objects.get_or_create(libelle=libelle)[0]
            utilisateurs[libelle] = User.objects.create_user(username=f"couv-{i}", password="x", role=role)
            utilisateurs[f"inactif-{libelle}"] = User.objects.create_user(
                username=f"couv-inactif-{i}", password="x", role=role, statut="INACTIF"
            )
        for ressource, actions in ATTENDU.items():
            for action, roles in actions.items():
                vue = type("VueFactice", (), {"ressource": ressource, "action": action})()
                for libelle in (A, D, G, E):
                    with self.subTest(ressource=ressource, action=action, role=libelle):
                        requete = factory.get("/")
                        requete.user = utilisateurs[libelle]
                        self.assertEqual(PermissionMatrice().has_permission(requete, vue), libelle in roles)
                        requete.user = utilisateurs[f"inactif-{libelle}"]
                        self.assertFalse(PermissionMatrice().has_permission(requete, vue))
