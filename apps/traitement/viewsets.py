from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import (
    IsAdministrateurOuGestionnaire,
    IsTousRolesActifs,
)

from .models import Traitement
from .serializers import ApercuClotureTraitementSerializer, TraitementSerializer
from .services import (
    cloturer_traitement,
    effectifs_traitement,
    preparer_cloture_traitement,
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
        # L'Employé peut consulter et créer un traitement, et voir le résumé
        # de clôture ; il ne peut pas le clôturer.
        if self.action in [
            "list",
            "retrieve",
            "create",
            "effectifs",
            "apercu_cloture",
        ]:
            return [IsTousRolesActifs()]

        # Seuls Admin et Gestionnaire clôturent, modifient ou suppriment.
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
        """Clôture confirmée depuis la boîte de validation.

        Les hapas sont bloqués jusqu'à la mise en stock ; le frontend propose
        ensuite de mettre le lot en stock (formulaire de mise en stock) ou non.
        """
        self.get_object()
        cloturer_traitement(traitement_id=pk)
        traitement = self.get_object()
        return Response(
            {
                **self.get_serializer(traitement).data,
                "message": (
                    f"Le traitement du lot {traitement.lot.reference} a bien été clôturé."
                ),
                "mise_en_stock_possible": True,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="apercu-cloture")
    def apercu_cloture(self, request, pk=None):
        """Résumé en lecture seule de la boîte de validation ; rien n'est écrit."""
        contexte = preparer_cloture_traitement(self.get_object())
        return Response(ApercuClotureTraitementSerializer(contexte).data)

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