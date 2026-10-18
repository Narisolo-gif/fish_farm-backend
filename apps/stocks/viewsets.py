
from rest_framework import status, viewsets
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from .models import MiseEnStock
from .serializers import MiseEnStockSerializer
from .services import supprimer_mise_en_stock


class StockPermission(BasePermission):
    """Permissions du module stocks selon le rôle et l'action."""

    ROLES_GESTIONNAIRES = {"Administrateur", "Gestionnaire"}
    ROLES_AUTORISES = ROLES_GESTIONNAIRES | {"Employé"}

    def has_permission(self, request, view):
        user = request.user

        # Un utilisateur doit être connecté et actif dans les deux systèmes.
        if not user or not user.is_authenticated:
            return False

        if (
            not user.is_active
            or getattr(user, "statut", None) != "ACTIF"
        ):
            return False

        role = getattr(user, "role", None)
        role_libelle = getattr(role, "libelle", None)

        if role_libelle not in self.ROLES_AUTORISES:
            return False

        # L'Employé peut uniquement créer une mise en stock.
        if role_libelle == "Employé":
            return view.action == "create"

        # Administrateur et Gestionnaire ont accès aux opérations CRUD.
        return role_libelle in self.ROLES_GESTIONNAIRES


class MiseEnStockViewSet(viewsets.ModelViewSet):
    """API CRUD des mises en stock."""

    serializer_class = MiseEnStockSerializer
    permission_classes = [StockPermission]

    queryset = MiseEnStock.objects.select_related(
        "lot",
        "bassin",
        "utilisateur",
        "lot__traitement",
    ).all()

    def perform_destroy(self, instance):
        # Le service sécurise la suppression et la transition du lot.
        supprimer_mise_en_stock(instance.pk)
