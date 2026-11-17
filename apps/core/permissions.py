from rest_framework.permissions import BasePermission

from .roles import (
    ADMINISTRATEUR,
    COMPTES,
    DIRECTEUR,
    GESTIONNAIRE,
    PILOTAGE,
    TERRAIN,
    TOUS,
)


# --- Matrice des permissions (voir context/CONCEPTION_MATRICE_PERMISSIONS.md) ---

LECTURE = {"list": TOUS, "retrieve": TOUS}
CRUD_PILOTAGE = {
    **LECTURE,
    "create": PILOTAGE,
    "update": PILOTAGE,
    "partial_update": PILOTAGE,
    "destroy": PILOTAGE,
}
ADMIN_SEUL = frozenset({ADMINISTRATEUR})

# {ressource: {action DRF: rôles autorisés}}. Une action absente est refusée.
MATRICE = {
    # Comptes
    "roles": {
        **LECTURE,
        "create": ADMIN_SEUL,
        "update": ADMIN_SEUL,
        "partial_update": ADMIN_SEUL,
        "destroy": ADMIN_SEUL,
    },
    "utilisateurs": {
        "list": frozenset({ADMINISTRATEUR, DIRECTEUR, GESTIONNAIRE}),
        "retrieve": frozenset({ADMINISTRATEUR, DIRECTEUR, GESTIONNAIRE}),
        "create": COMPTES,
        "update": COMPTES,
        "partial_update": COMPTES,
        "destroy": COMPTES,
        "reset_password": COMPTES,
        "change_password": TOUS,
    },
    # Référentiels
    "bassins": CRUD_PILOTAGE,
    "hapas": CRUD_PILOTAGE,
    "types_provende": CRUD_PILOTAGE,
    "provendes": CRUD_PILOTAGE,
    "causes_mortalite": CRUD_PILOTAGE,
    "categories_perte": CRUD_PILOTAGE,
    # Chaîne de démarrage
    "reproductions": {**CRUD_PILOTAGE, "desactiver": PILOTAGE},
    "pontes": {**CRUD_PILOTAGE, "desactiver": PILOTAGE},
    "eclosions": {
        **CRUD_PILOTAGE,
        "desactiver": PILOTAGE,
        "getbyref_pontes": TOUS,
        "getbydateEclosion": TOUS,
    },
    "lots": CRUD_PILOTAGE,
    # Traitement et stock
    "traitement": {
        **CRUD_PILOTAGE,
        "effectifs": TOUS,
        "apercu_cloture": TOUS,
        "cloturer": PILOTAGE,
    },
    "stocks": {
        **LECTURE,
        "create": PILOTAGE,
        "update": PILOTAGE,
        "partial_update": PILOTAGE,
        # Outil de nettoyage des tests : Administrateur seul.
        "destroy": ADMIN_SEUL,
    },
    # Journaux de terrain
    "mouvements": {
        **LECTURE,
        "solde": TOUS,
        "par_hapa": TOUS,
        "annuler": PILOTAGE,
    },
    "evenements_mortalite_perte": {
        **LECTURE,
        "totaux": TOUS,
        "create": TERRAIN,
        "annuler": PILOTAGE,
    },
    "consommations_provende": {
        **LECTURE,
        "totaux_journaliers": TOUS,
        "jours_manquants_action": TOUS,
        "create": TERRAIN,
        "annuler": PILOTAGE,
    },
    "environnement": {
        **LECTURE,
        "historique_bassin": TOUS,
        "derniere_mesure_bassin": TOUS,
        "dernieres_mesures": TOUS,
        "create": TERRAIN,
        "update": PILOTAGE,
        "partial_update": PILOTAGE,
        "destroy": PILOTAGE,
    },
    "sorties_stock": {**LECTURE, "annuler": PILOTAGE},
    # Grossissement
    "lots_grossissement": {
        **LECTURE,
        "suivi": TOUS,
        "origine": TOUS,
        "apercu_cloture": TOUS,
        "historique_clotures": TOUS,
        "apercu_sortie": PILOTAGE,
        "create": PILOTAGE,
        "partial_update": PILOTAGE,
        "cloturer": PILOTAGE,
        "evacuer": PILOTAGE,
    },
    "pesees": {
        **LECTURE,
        "create": TERRAIN,
        "apercu": TERRAIN,
        "partial_update": PILOTAGE,
        "annuler": PILOTAGE,
    },
}


def role_actif(user):
    """Libellé du rôle d'un utilisateur connecté, actif et au statut ACTIF ; sinon None."""
    if not user or not user.is_authenticated:
        return None
    if not user.is_active or getattr(user, "statut", None) != "ACTIF":
        return None
    role = getattr(user, "role", None)
    return role.libelle if role is not None else None


class UtilisateurActif(BasePermission):
    """Tout utilisateur connecté, actif et doté d'un rôle (me, logout, mot de passe)."""

    def has_permission(self, request, view):
        return role_actif(request.user) is not None


class PermissionMatrice(BasePermission):
    """Applique MATRICE : `view.ressource` et `view.action` ; refus par défaut."""

    def has_permission(self, request, view):
        role = role_actif(request.user)
        if role is None:
            return False
        action = getattr(view, "action", None)
        methodes = getattr(view, "http_method_names", None)
        if action is None or (
            methodes is not None and request.method.lower() not in methodes
        ):
            # Méthode non routée ou exclue du viewset : DRF répond 405 (aucun
            # droit n'est accordé, la requête n'atteint pas le traitement).
            return True
        if action == "metadata":
            # OPTIONS : description de la route, sans donnée métier.
            return True
        regles = MATRICE.get(getattr(view, "ressource", None), {})
        return role in regles.get(action, frozenset())
