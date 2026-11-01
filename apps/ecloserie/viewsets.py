
from django.db import transaction
from django.utils.dateparse import parse_date

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from .models import Eclosion
from .serializers import EclosionSerializer


class IsRoleEcloserieAutorise(BasePermission):
    """
    Autorise les utilisateurs actifs selon leur rôle métier.
    """

    roles_autorises = {
        "Administrateur",
        "Gestionnaire",
        "Employé",
    }

    def has_permission(self, request, view):
        user = request.user

        return bool(
            user
            and user.is_authenticated
            and user.statut == "ACTIF"
            and user.role
            and user.role.libelle in self.roles_autorises
        )


class IsAdministrateurOuGestionnaireEcloserie(BasePermission):
    """
    Réserve la suppression aux administrateurs et gestionnaires actifs.
    """

    def has_permission(self, request, view):
        user = request.user

        return bool(
            user
            and user.is_authenticated
            and user.statut == "ACTIF"
            and user.role
            and user.role.libelle in {
                "Administrateur",
                "Gestionnaire",
            }
        )


class EclosionViewSet(viewsets.ModelViewSet):
    serializer_class = EclosionSerializer

    queryset = Eclosion.objects.select_related(
        "ponte",
        "ponte__reproduction",
        "ponte__reproduction__bassin",
        "utilisateur",
    ).all()

    def get_permissions(self):
        if self.action in ["destroy", "desactiver"]:
            permission_classes = [
                IsAuthenticated,
                IsAdministrateurOuGestionnaireEcloserie,
            ]
        else:
            permission_classes = [
                IsAuthenticated,
                IsRoleEcloserieAutorise,
            ]

        return [permission() for permission in permission_classes]

    def get_queryset(self):
        queryset = super().get_queryset()

        reference_ponte = self.request.query_params.get(
            "reference_ponte"
        )
        date_eclosion = self.request.query_params.get(
            "date_eclosion"
        )

        if reference_ponte:
            queryset = queryset.filter(
                ponte__reference=reference_ponte
            )

        if date_eclosion:
            date_value = parse_date(date_eclosion)

            if date_value is None:
                raise ValidationError({
                    "date_eclosion": (
                        "Format invalide. Utilisez YYYY-MM-DD."
                    )
                })

            queryset = queryset.filter(
                date_eclosion=date_value
            )

        return queryset

    @transaction.atomic
    def perform_create(self, serializer):
        serializer.save(utilisateur=self.request.user)

    @transaction.atomic
    def perform_update(self, serializer):
        serializer.save()

    @action(
        detail=True,
        methods=["patch"],
        url_path="desactiver",
    )
    @transaction.atomic
    def desactiver(self, request, pk=None):
        eclosion = self.get_object()

        if eclosion.statut == Eclosion.Statut.INACTIF:
            return Response(
                {
                    "detail": "Cette éclosion est déjà inactive."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        eclosion.statut = Eclosion.Statut.INACTIF
        eclosion.save(update_fields=["statut", "updated_at"])

        return Response(
            {
                "detail": "Éclosion désactivée avec succès.",
                "id": eclosion.id,
                "reference": eclosion.reference,
                "statut": eclosion.statut,
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False,
        methods=["get"],
        url_path="getbyref_pontes",
    )
    def getbyref_pontes(self, request):
        reference_ponte = request.query_params.get(
            "reference_ponte"
        )

        if not reference_ponte:
            raise ValidationError({
                "reference_ponte": (
                    "Ce paramètre est obligatoire."
                )
            })

        queryset = self.get_queryset().filter(
            ponte__reference=reference_ponte
        )

        serializer = self.get_serializer(
            queryset,
            many=True,
        )
        return Response(serializer.data)

    @action(
        detail=False,
        methods=["get"],
        url_path="getbydateEclosion",
    )
    def getbydateEclosion(self, request):
        date_eclosion = request.query_params.get(
            "date_eclosion"
        )

        if not date_eclosion:
            raise ValidationError({
                "date_eclosion": (
                    "Ce paramètre est obligatoire."
                )
            })

        date_value = parse_date(date_eclosion)

        if date_value is None:
            raise ValidationError({
                "date_eclosion": (
                    "Format invalide. Utilisez YYYY-MM-DD."
                )
            })

        queryset = self.get_queryset().filter(
            date_eclosion=date_value
        )

        serializer = self.get_serializer(
            queryset,
            many=True,
        )
        return Response(serializer.data)
