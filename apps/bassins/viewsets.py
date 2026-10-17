from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import (
    IsAdministrateur,
    IsAdministrateurOuGestionnaire,
)

from .models import Bassin, Hapa
from .serializers import BassinSerializer, HapaSerializer


class BassinViewSet(viewsets.ModelViewSet):
    """
    Gestion des bassins.

    Administrateur :
        - consulter
        - créer
        - modifier
        - mettre hors service

    Gestionnaire :
        - consulter

    Employé :
        - aucun accès
    """

    queryset = Bassin.objects.prefetch_related("hapas").all()
    serializer_class = BassinSerializer

    def get_permissions(self):
        """
        Détermine les permissions selon l'opération demandée.
        """

        if self.action in ["list", "retrieve", "create"]:
            return [IsAdministrateurOuGestionnaire()]

        return [IsAdministrateur()]

    def get_queryset(self):
        """
        Retourne les bassins à consulter.

        Les filtres categorie et statut sont optionnels.
        Les filtres sont insensibles à la casse.
        """

        queryset = Bassin.objects.prefetch_related("hapas").all()

        categorie = self.request.query_params.get("categorie")
        statut = self.request.query_params.get("statut")

        if categorie:
            queryset = queryset.filter(
                categorie__iexact=categorie
            )

        if statut:
            queryset = queryset.filter(
                statut__iexact=statut
            )

        return queryset
    
    def _desactiver_hapas_si_necessaire(
        self,
        bassin,
        ancien_statut,
    ):
        """
        Désactive automatiquement tous les hapas actifs
        lorsque le bassin vient de passer à INACTIF.
        """

        if (
            ancien_statut != Bassin.Statut.INACTIF
            and bassin.statut == Bassin.Statut.INACTIF
        ):
            bassin.hapas.filter(
                statut=Hapa.Statut.ACTIF
            ).update(
                statut=Hapa.Statut.INACTIF
            )

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        """
        Modifie complètement un bassin.

        Si le bassin passe à INACTIF, tous ses hapas actifs
        sont automatiquement désactivés.
        """

        bassin = self.get_object()
        ancien_statut = bassin.statut

        response = super().update(
            request,
            *args,
            **kwargs,
        )

        bassin.refresh_from_db()

        self._desactiver_hapas_si_necessaire(
            bassin,
            ancien_statut,
        )

        bassin.refresh_from_db()

        serializer = self.get_serializer(bassin)

        return Response(
            serializer.data,
            status=response.status_code,
        )

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        """
        Modifie partiellement un bassin.

        Si le statut passe à INACTIF, tous les hapas actifs
        sont automatiquement désactivés.
        """

        bassin = self.get_object()
        ancien_statut = bassin.statut

        response = super().partial_update(
            request,
            *args,
            **kwargs,
        )

        bassin.refresh_from_db()

        self._desactiver_hapas_si_necessaire(
            bassin,
            ancien_statut,
        )

        bassin.refresh_from_db()

        serializer = self.get_serializer(bassin)

        return Response(
            serializer.data,
            status=response.status_code,
        )
    


class HapaViewSet(viewsets.ModelViewSet):
    """
    ViewSet dédié à la gestion des hapas rattachés aux bassins.

    Les hapas sont manipulés uniquement dans le contexte
    d'un bassin.

    Administrateur :
        - consulter
        - modifier
        - désactiver

    Gestionnaire :
        - consulter

    Employé :
        - aucun accès
    """

    queryset = Hapa.objects.select_related("bassin").all()
    serializer_class = HapaSerializer

    def get_queryset(self):
        """
        Limite les hapas au bassin indiqué dans l'URL.

        Cela garantit qu'un hapa ne peut être consulté ou
        modifié à travers un bassin auquel il n'appartient pas.
        """

        bassin_id = self.kwargs.get("bassin_id")

        queryset = self.queryset.filter(
            bassin_id=bassin_id
        )

        return queryset

    def get_permissions(self):
        """
        Consultation :
            Administrateur + Gestionnaire

        Création, modification et désactivation :
            Administrateur uniquement.
        """

        if self.action in ["list", "retrieve"]:
            return [IsAdministrateurOuGestionnaire()]

        return [IsAdministrateur()]

    def create(self, request, *args, **kwargs):
        """
        Crée un hapa directement dans le bassin indiqué
        par l'URL.

        Le bassin est imposé par l'URL et ne peut pas être
        fourni par le client.
        """

        bassin_id = self.kwargs.get("bassin_id")

        try:
            bassin = Bassin.objects.get(pk=bassin_id)
        except Bassin.DoesNotExist:
            return Response(
                {
                    "detail": "Bassin introuvable."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = self.get_serializer(
            data=request.data
        )

        serializer.is_valid(raise_exception=True)

        serializer.save(bassin=bassin)

        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        """
        Désactive le hapa au lieu de le supprimer physiquement.
        """

        hapa = self.get_object()

        hapa.statut = Hapa.Statut.INACTIF

        hapa.save(
            update_fields=["statut"]
        )

        serializer = self.get_serializer(hapa)

        return Response(serializer.data)