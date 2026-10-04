from rest_framework.permissions import BasePermission


class IsAdministrateur(BasePermission):
    """
    Autorise uniquement les utilisateurs ayant le rôle Administrateur.
    """

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if not user.is_active:
            return False

        return (
            user.role is not None
            and user.role.libelle == "Administrateur"
        )


class IsAdministrateurOuGestionnaire(BasePermission):
    """
    Autorise les utilisateurs ayant le rôle :
    - Administrateur
    - Gestionnaire

    Les utilisateurs Employé n'ont pas accès.
    """

    ROLES_AUTORISES = {
        "Administrateur",
        "Gestionnaire",
    }

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if not user.is_active:
            return False

        return (
            user.role is not None
            and user.role.libelle in self.ROLES_AUTORISES
        )