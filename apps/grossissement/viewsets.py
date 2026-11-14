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

from .models import LotGrossissement, Pesee
from .serializers import (
    ClotureLotGrossissementSerializer,
    LotGrossissementSerializer,
    OrigineLotGrossissementSerializer,
    PeseeEcritureSerializer,
    PeseeSerializer,
)
from .services import (
    EchantillonNonConforme,
    cloturer_lot_grossissement,
    creer_pesee,
    modifier_pesee,
    supprimer_pesee,
)


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

def _reponse_echantillon_non_conforme(exc):
    return Response(
        {
            "echantillon_non_conforme": exc.message,
            "lignes_non_conformes": exc.lignes_non_conformes,
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


class PeseeViewSet(viewsets.ModelViewSet):
    """
    Pesées sur échantillon des lots de grossissement.

    Administrateur, Gestionnaire : toutes les actions.
    Employé : création, liste et lecture.
    """

    queryset = (
        Pesee.objects.select_related("bassin", "utilisateur")
        .prefetch_related("lignes__lot_grossissement")
        .all()
    )
    serializer_class = PeseeSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in {"partial_update", "destroy"}:
            return [
                IsAuthenticated(),
                IsTousRolesActifs(),
                IsAdministrateurOuGestionnaire(),
            ]
        return [IsAuthenticated(), IsTousRolesActifs()]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        for parametre, champ in (
            ("bassin", "bassin_id"),
            ("lot", "lignes__lot_grossissement_id"),
        ):
            valeur = params.get(parametre)
            if valeur:
                if not valeur.isdigit():
                    raise ValidationError({parametre: "Identifiant invalide."})
                queryset = queryset.filter(**{champ: valeur})

        dates = {}
        for parametre, lookup in (
            ("date_min", "date__gte"),
            ("date_max", "date__lte"),
        ):
            valeur = params.get(parametre)
            date = parse_date(valeur) if valeur else None
            if valeur and date is None:
                raise ValidationError({
                    parametre: "Format invalide. Utilisez YYYY-MM-DD."
                })
            if date:
                dates[parametre] = date
                queryset = queryset.filter(**{lookup: date})
        if (
            "date_min" in dates
            and "date_max" in dates
            and dates["date_max"] < dates["date_min"]
        ):
            raise ValidationError({
                "date_max": "La date de fin doit être égale ou postérieure au début."
            })
        return queryset.distinct().order_by("-date", "-id")

    def _relire(self, pesee_id):
        return Response(
            PeseeSerializer(
                Pesee.objects.select_related("bassin", "utilisateur")
                .prefetch_related("lignes__lot_grossissement")
                .get(pk=pesee_id)
            ).data
        )

    def create(self, request, *args, **kwargs):
        serializer = PeseeEcritureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        try:
            pesee = creer_pesee(
                bassin_id=donnees["id_bassin"],
                date_pesee=donnees["date"],
                lignes=donnees["lignes"],
                observation=donnees.get("observation", ""),
                confirmer=donnees.get("confirmer_echantillon_non_conforme", False),
                utilisateur=request.user,
            )
        except EchantillonNonConforme as exc:
            return _reponse_echantillon_non_conforme(exc)
        reponse = self._relire(pesee.pk)
        reponse.status_code = status.HTTP_201_CREATED
        return reponse

    def partial_update(self, request, *args, **kwargs):
        pesee = self.get_object()
        serializer = PeseeEcritureSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        try:
            modifier_pesee(
                pesee.pk,
                donnees,
                confirmer=donnees.get("confirmer_echantillon_non_conforme", False),
            )
        except EchantillonNonConforme as exc:
            return _reponse_echantillon_non_conforme(exc)
        return self._relire(pesee.pk)

    def destroy(self, request, *args, **kwargs):
        supprimer_pesee(self.get_object().pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
