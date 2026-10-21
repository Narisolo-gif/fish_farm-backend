from django.db import transaction

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.bassins.models import Bassin
from apps.core.permissions import (
    IsAdministrateur,
    IsAdministrateurOuGestionnaire,
)
from apps.effectifs.models import MouvementEffectif
from apps.effectifs.services import (
    enregistrer_mouvement_operation,
    operation_a_mouvements,
)

from .models import Ponte, Reproduction
from .serializers import (
    PonteSerializer,
    ReproductionSerializer,
)

class ReproductionViewSet(viewsets.ModelViewSet):
    queryset = (
        Reproduction.objects
        .select_related("bassin")
        .prefetch_related("pontes")
        .all()
    )

    serializer_class = ReproductionSerializer

    def get_permissions(self):
        if self.action in [
              "list",
            "retrieve",
            "create",
        ]:
            permission_classes = [
                IsAuthenticated,
                IsAdministrateurOuGestionnaire,
            ]
        else:
            permission_classes = [
                IsAuthenticated,
                IsAdministrateur,
            ]

        return [
            permission()
            for permission in permission_classes
        ]

    def get_queryset(self):
        queryset = super().get_queryset()

        bassin_id = self.request.query_params.get("bassin")
        statut = self.request.query_params.get("statut")
        espece = self.request.query_params.get("espece")

        if bassin_id:
            queryset = queryset.filter(
                bassin_id=bassin_id
            )

        if statut:
            queryset = queryset.filter(
                statut__iexact=statut
            )

        if espece:
            queryset = queryset.filter(
                espece__iexact=espece
            )

        return queryset

    @transaction.atomic
    def perform_create(self, serializer):
        bassin_id = serializer.validated_data["bassin"].id

        # Verrouillage du bassin pour éviter deux créations
        # concurrentes de reproductions actives.
        Bassin.objects.select_for_update().get(
            id=bassin_id
        )

        reproduction = serializer.save()
        enregistrer_mouvement_operation(
            operation=reproduction,
            bassin=reproduction.bassin,
            espece=reproduction.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            quantite=(
                reproduction.quantite_male
                + reproduction.quantite_femelle
            ),
            date_evenement=reproduction.date_ajout,
            saisi_par=self.request.user,
        )

    @transaction.atomic
    def perform_update(self, serializer):
        bassin = serializer.instance.bassin

        # Le bassin est verrouillé pendant la modification
        # afin de garantir la cohérence de la capacité.
        Bassin.objects.select_for_update().get(
            id=bassin.id
        )

        instance = serializer.instance
        if operation_a_mouvements(instance):
            for field in (
                "quantite_male",
                "quantite_femelle",
                "espece",
                "date_ajout",
                "bassin",
            ):
                if (
                    field in serializer.validated_data
                    and serializer.validated_data[field]
                    != getattr(instance, field)
                ):
                    raise ValidationError({
                        field: (
                            "Les données qui déterminent le mouvement d’une "
                            "reproduction enregistrée ne peuvent pas être modifiées."
                        )
                    })
        serializer.save()

    def perform_destroy(self, instance):
        if operation_a_mouvements(instance):
            raise ValidationError({
                "detail": (
                    "Une reproduction référencée par le registre ne peut "
                    "pas être supprimée."
                )
            })
        instance.delete()

    @action(
        detail=True,
        methods=["patch"],
        url_path="desactiver",
    )
    @transaction.atomic
    def desactiver(self, request, pk=None):
        reproduction = self.get_object()

        if reproduction.statut == Reproduction.Statut.INACTIF:
            return Response(
                {
                    "detail": "Cette reproduction est déjà inactive."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        reproduction.statut = Reproduction.Statut.INACTIF
        reproduction.save(
            update_fields=["statut"]
        )

        return Response(
            ReproductionSerializer(
                reproduction,
                context={"request": request},
            ).data,
            status=status.HTTP_200_OK,
        )


class PonteViewSet(viewsets.ModelViewSet):
    queryset = (
        Ponte.objects
        .select_related(
            "reproduction",
            "reproduction__bassin",
        )
        .all()
    )

    serializer_class = PonteSerializer

    def get_permissions(self):
        if self.action in [
            "list",
            "retrieve",
        ]:
            permission_classes = [
                IsAuthenticated,
                IsAdministrateurOuGestionnaire,
            ]
        else:
            permission_classes = [
                IsAuthenticated,
                IsAdministrateur,
            ]

        return [
            permission()
            for permission in permission_classes
        ]

    def get_queryset(self):
        queryset = super().get_queryset()

        bassin_id = self.request.query_params.get("bassin")
        reproduction_id = self.request.query_params.get(
            "reproduction"
        )

        if bassin_id:
            queryset = queryset.filter(
                reproduction__bassin_id=bassin_id
            )

        if reproduction_id:
            queryset = queryset.filter(
                reproduction_id=reproduction_id
            )

        return queryset

    @transaction.atomic
    def perform_create(self, serializer):
        """
        Verrouille le bassin pendant la création afin d'éviter
        les incohérences liées aux opérations concurrentes.

        La résolution de la reproduction active est effectuée
        par PonteSerializer.create().
        """
        bassin_id = serializer.validated_data["bassin"].id

        Bassin.objects.select_for_update().get(
            id=bassin_id
        )

        serializer.save()