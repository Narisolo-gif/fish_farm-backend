from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import (
    IsAdministrateurOuGestionnaire,
    IsTousRolesActifs,
)
from apps.effectifs.models import SortieStock
from apps.lots.models import Lot

from .models import LotGrossissement
from .serializers import (
    ClotureLotGrossissementSerializer,
    LotGrossissementSerializer,
    OrigineLotGrossissementSerializer,
)
from .services import cloturer_lot_grossissement


class LotGrossissementViewSet(viewsets.ModelViewSet):
    queryset = (
        LotGrossissement.objects.select_related(
            "lot_origine",
            "sortie_stock",
            "bassin",
            "utilisateur",
            "lot_origine__traitement__bassin",
            "lot_origine__mise_en_stock__bassin",
        )
        .prefetch_related(
            "lot_origine__traitement__lignes_hapas__hapa",
            "lot_origine__mise_en_stock__lignes_hapas__hapa",
        )
        .all()
    )
    serializer_class = LotGrossissementSerializer
    http_method_names = ["get", "patch", "post", "head", "options"]

    def get_permissions(self):
        if self.action in {"list", "retrieve", "origine"}:
            return [IsAuthenticated(), IsTousRolesActifs()]
        return [IsAuthenticated(), IsAdministrateurOuGestionnaire()]

    def create(self, request, *args, **kwargs):
        raise MethodNotAllowed("POST")

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        for parametre, champ in (
            ("statut", "statut"),
            ("espece", "espece"),
        ):
            valeur = params.get(parametre)
            if valeur:
                options = (
                    LotGrossissement.Statut.values
                    if champ == "statut"
                    else Lot.Espece.values
                )
                if valeur not in options:
                    raise ValidationError({parametre: "Valeur inconnue."})
                queryset = queryset.filter(**{champ: valeur})

        for parametre, champ in (
            ("bassin", "bassin_id"),
            ("lot_origine", "lot_origine_id"),
        ):
            valeur = params.get(parametre)
            if valeur:
                if not valeur.isdigit():
                    raise ValidationError({parametre: "Identifiant invalide."})
                queryset = queryset.filter(**{champ: valeur})

        for parametre, lookup in (
            ("date_min", "date_mise_en_place__date__gte"),
            ("date_max", "date_mise_en_place__date__lte"),
        ):
            valeur = params.get(parametre)
            date = parse_date(valeur) if valeur else None
            if valeur and date is None:
                raise ValidationError({
                    parametre: "Format invalide. Utilisez YYYY-MM-DD."
                })
            if date:
                queryset = queryset.filter(**{lookup: date})

        date_min = parse_date(params.get("date_min", ""))
        date_max = parse_date(params.get("date_max", ""))
        if date_min and date_max and date_max < date_min:
            raise ValidationError({
                "date_max": "La date de fin doit être égale ou postérieure au début."
            })
        return queryset.order_by("-created_at", "-id")

    @action(detail=True, methods=["post"], url_path="cloturer")
    def cloturer(self, request, pk=None):
        serializer = ClotureLotGrossissementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        lot = cloturer_lot_grossissement(
            self.get_object().pk,
            motif=donnees["motif"],
            poids_final_g=donnees["poids_final_g"],
            date_cloture=donnees.get("date_cloture"),
            utilisateur=request.user,
        )
        return Response(self.get_serializer(lot).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="origine")
    def origine(self, request, pk=None):
        lot = self.get_object()
        serializer = OrigineLotGrossissementSerializer(lot)
        return Response(serializer.data)