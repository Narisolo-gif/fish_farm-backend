from django.db.models.deletion import ProtectedError

from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from apps.core.permissions import (
    IsAdministrateurOuGestionnaire,
    IsTousRolesActifs,
)

from .models import Provende, TypeProvende
from .serializers import ProvendeSerializer, TypeProvendeSerializer


class ProvendeViewSet(viewsets.ModelViewSet):
    """API CRUD des provendes."""

    queryset = Provende.objects.select_related("type_provende").all()
    serializer_class = ProvendeSerializer

    def get_permissions(self):
        # Tous les rôles actifs peuvent consulter.
        if self.action in ["list", "retrieve"]:
            return [IsTousRolesActifs()]

        # Seuls Admin et Gestionnaire peuvent modifier les données.
        return [IsAdministrateurOuGestionnaire()]

    def perform_destroy(self, instance):
        """Transforme une suppression protégée en erreur API contrôlée."""

        try:
            instance.delete()
        except ProtectedError as exc:
            raise ValidationError({
                "detail": (
                    "Cette provende est utilisée par un traitement "
                    "et ne peut pas être supprimée."
                )
            }) from exc


class TypeProvendeViewSet(viewsets.ModelViewSet):
    """API CRUD des types de provende."""

    queryset = TypeProvende.objects.all()
    serializer_class = TypeProvendeSerializer

    def get_permissions(self):
        # Tous les rôles actifs peuvent consulter les types.
        if self.action in ["list", "retrieve"]:
            return [IsTousRolesActifs()]

        return [IsAdministrateurOuGestionnaire()]

    def perform_destroy(self, instance):
        """Empêche la suppression d'un type encore référencé."""

        try:
            instance.delete()
        except ProtectedError as exc:
            raise ValidationError({
                "detail": (
                    "Ce type de provende est associé à une ou plusieurs "
                    "provendes et ne peut pas être supprimé."
                )
            }) from exc