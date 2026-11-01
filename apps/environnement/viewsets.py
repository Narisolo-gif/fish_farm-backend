from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import (
    IsAdministrateur,
    IsAdministrateurOuGestionnaire,
)

from .models import ParametreEnvironnemental
from .serializers import ParametreEnvironnementalSerializer


class ParametreEnvironnementalViewSet(
    viewsets.ModelViewSet
):
    """
    Gestion des paramètres environnementaux.

    Administrateur :
        - consulter
        - créer
        - modifier
        - supprimer

    Gestionnaire :
        - consulter

    Employé :
        - aucun accès
    """

    queryset = (
        ParametreEnvironnemental.objects
        .select_related("bassin", "utilisateur")
        .all()
    )

    serializer_class = ParametreEnvironnementalSerializer

    def get_permissions(self):
        """
        Détermine les permissions selon l'action.
        """

        if self.action in [
            "list",
            "retrieve",
            "historique_bassin",
            "derniere_mesure_bassin",
            "dernieres_mesures",
        ]:
            return [
                IsAdministrateurOuGestionnaire()
            ]

        return [IsAdministrateur()]

    def get_queryset(self):
        """
        Permet de filtrer les mesures par bassin.

        Exemple :
            ?bassin=1
        """

        queryset = self.queryset

        bassin_id = self.request.query_params.get(
            "bassin"
        )

        if bassin_id:
            queryset = queryset.filter(
                bassin_id=bassin_id
            )

        return queryset

    def perform_create(self, serializer):
        serializer.save(utilisateur=self.request.user)

    @action(
        detail=False,
        methods=["get"],
        url_path=r"bassin/(?P<bassin_id>\d+)",
    )
    def historique_bassin(
        self,
        request,
        bassin_id=None,
    ):
        """
        Retourne l'historique environnemental
        d'un bassin.
        """

        mesures = self.get_queryset().filter(
            bassin_id=bassin_id
        )

        serializer = self.get_serializer(
            mesures,
            many=True,
        )

        return Response(serializer.data)

    @action(
        detail=False,
        methods=["get"],
        url_path=r"bassin/(?P<bassin_id>\d+)/latest",
    )
    def derniere_mesure_bassin(
        self,
        request,
        bassin_id=None,
    ):
        """
        Retourne uniquement la dernière mesure
        environnementale d'un bassin.
        """

        mesure = (
            self.get_queryset()
            .filter(bassin_id=bassin_id)
            .first()
        )

        if mesure is None:
            return Response(
                {
                    "detail": (
                        "Aucune mesure environnementale "
                        "n'est enregistrée pour ce bassin."
                    )
                },
                status=404,
            )

        serializer = self.get_serializer(mesure)

        return Response(serializer.data)

    @action(
        detail=False,
        methods=["get"],
        url_path="latest",
    )
    def dernieres_mesures(
        self,
        request,
    ):
        """
        Retourne la dernière mesure de chaque bassin.

        Cet endpoint est destiné notamment au dashboard
        « Paramètres environnementaux · Tous les bassins ».
        """

        mesures = []

        bassin_ids = (
            self.get_queryset()
            .values_list(
                "bassin_id",
                flat=True,
            )
            .distinct()
        )

        for bassin_id in bassin_ids:
            mesure = (
                self.get_queryset()
                .filter(bassin_id=bassin_id)
                .first()
            )

            if mesure:
                mesures.append(mesure)

        serializer = self.get_serializer(
            mesures,
            many=True,
        )

        return Response(serializer.data)