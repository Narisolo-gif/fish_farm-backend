from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import (
    IsAdministrateurOuGestionnaire,
    IsTousRolesActifs,
)

from .models import Traitement
from .serializers import TraitementSerializer
from .services import (
    cloturer_traitement,
    effectifs_traitement,
    supprimer_traitement,
)


class TraitementViewSet(viewsets.ModelViewSet):
    """API de gestion des traitements."""

    queryset = Traitement.objects.select_related(
        "lot",
        "bassin",
        "used_provende",
        "utilisateur",
        "used_provende__type_provende",
    ).prefetch_related("lignes_hapas__hapa").all().order_by("-created_at", "-id")

    serializer_class = TraitementSerializer

    def get_permissions(self):
        # L'Employé peut consulter, créer et clôturer un traitement.
        if self.action in [
            "list",
            "retrieve",
            "create",
            "cloturer",
            "effectifs",
        ]:
            return [IsTousRolesActifs()]

        # Seuls Admin et Gestionnaire peuvent modifier ou supprimer.
        return [IsAdministrateurOuGestionnaire()]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["include_hapa_effectifs"] = self.action == "retrieve"
        return context

    @action(
        detail=True,
        methods=["post"],
        url_path="cloturer",
    )
    def cloturer(self, request, pk=None):
        """Endpoint dédié à la clôture du traitement."""
        self.get_object()
        cloturer_traitement(traitement_id=pk)

        return Response(
            self.get_serializer(self.get_object()).data,
            status=status.HTTP_200_OK,
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="effectifs",
    )
    def effectifs(self, request, pk=None):
        return Response(effectifs_traitement(self.get_object()))

    def perform_destroy(self, instance):
        # Le service supprime le traitement et réinitialise le stade du lot.
        supprimer_traitement(instance.pk)