from rest_framework.permissions import BasePermission


class IsAdministrateur(BasePermission):
    """
    Autorise uniquement les utilisateurs actifs
    ayant le rôle Administrateur.
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