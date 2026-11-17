"""Rôles métier : seule source des libellés de rôle (voir CONCEPTION_MATRICE_PERMISSIONS)."""

# Développeur, compte de support : accès à tout.
ADMINISTRATEUR = "Administrateur"
# Directeur des opérations : décisions de production, corrections, référentiels.
DIRECTEUR = "Directeur"
# Gérante : comptes utilisateurs ; consultation de la production.
GESTIONNAIRE = "Gestionnaire"
# Personnel de terrain : saisies des faits observés.
EMPLOYE = "Employé"

ROLES = (ADMINISTRATEUR, DIRECTEUR, GESTIONNAIRE, EMPLOYE)

TOUS = frozenset(ROLES)
PILOTAGE = frozenset({ADMINISTRATEUR, DIRECTEUR})
TERRAIN = frozenset({ADMINISTRATEUR, DIRECTEUR, EMPLOYE})
COMPTES = frozenset({ADMINISTRATEUR, GESTIONNAIRE})
