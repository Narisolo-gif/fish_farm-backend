from rest_framework import status, viewsets
from django.utils.dateparse import parse_date
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.permissions import PermissionMatrice

from .models import Traitement
from .serializers import ApercuClotureTraitementSerializer, TraitementSerializer
from .services import (
    cloturer_traitement,
    effectifs_traitement,
    preparer_cloture_traitement,
    supprimer_traitement,
)


def _date_cloture(valeur):
    """Date de clôture facultative (AAAA-MM-JJ) ; None = aujourd'hui."""
    if not valeur:
        return None
    try:
        jour = parse_date(str(valeur))
    except ValueError:
        jour = None
    if jour is None:
        raise ValidationError({"date_cloture": "Format invalide. Utilisez YYYY-MM-DD."})
    return jour


class TraitementViewSet(viewsets.ModelViewSet):
    """API de gestion des traitements."""

    ressource = "traitement"
    permission_classes = [PermissionMatrice]

    queryset = Traitement.objects.select_related(
        "lot",
        "bassin",
        "used_provende",
        "utilisateur",
        "used_provende__type_provende",
    ).prefetch_related("lignes_hapas__hapa").all().order_by("-created_at", "-id")

    serializer_class = TraitementSerializer

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
        cloturer_traitement(
            traitement_id=pk,
            date_cloture=_date_cloture(request.data.get("date_cloture")),
        )
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
        contexte = preparer_cloture_traitement(
            self.get_object(),
            date_cloture=_date_cloture(request.query_params.get("date_cloture")),
        )
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