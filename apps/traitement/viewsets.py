from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import (
    IsAdministrateurOuGestionnaire,
    IsTousRolesActifs,
)

from .models import Traitement
from .serializers import (
    ClotureTraitementSerializer,
    TraitementSerializer,
)
from .services import cloturer_traitement, supprimer_traitement


class TraitementViewSet(viewsets.ModelViewSet):
    """API de gestion des traitements."""

    queryset = Traitement.objects.select_related(
        "lot",
        "bassin",
        "used_provende",
        "utilisateur",
        "used_provende__type_provende",
    ).all()

    serializer_class = TraitementSerializer

    def get_permissions(self):
        # L'Employé peut consulter, créer et clôturer un traitement.
        if self.action in [
            "list",
            "retrieve",
            "create",
            "cloturer",
        ]:
            return [IsTousRolesActifs()]

        # Seuls Admin et Gestionnaire peuvent modifier ou supprimer.
        return [IsAdministrateurOuGestionnaire()]

    @action(
        detail=True,
        methods=["post"],
        url_path="cloturer",
    )
    def cloturer(self, request, pk=None):
        """Endpoint dédié à la clôture du traitement."""

        traitement = self.get_object()

        serializer = ClotureTraitementSerializer(
            data=request.data,
            context={"traitement": traitement},
        )
        serializer.is_valid(raise_exception=True)

        traitement = cloturer_traitement(
            traitement_id=traitement.pk,
            effectif_final=serializer.validated_data["effectif_final"],
        )

        return Response(
            self.get_serializer(traitement).data,
            status=status.HTTP_200_OK,
        )

    def perform_destroy(self, instance):
        # Le service supprime le traitement et réinitialise le stade du lot.
        supprimer_traitement(instance.pk)