
from django.db import IntegrityError, transaction

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.permissions import PermissionMatrice
from apps.ecloserie.models import Eclosion

from .models import Lot
from .serializers import LotSerializer


class LotViewSet(viewsets.ModelViewSet):
    ressource = "lots"
    permission_classes = [PermissionMatrice]
    serializer_class = LotSerializer

    queryset = Lot.objects.select_related(
        "eclosion",
        "eclosion__ponte",
        "eclosion__ponte__reproduction",
        "utilisateur_createur",
    ).all()

    @transaction.atomic
    def perform_create(self, serializer):
        eclosion_id = serializer.validated_data["eclosion"].pk

        # Verrouille l'éclosion durant la création.
        eclosion = (
            Eclosion.objects
            .select_for_update()
            .get(pk=eclosion_id)
        )

        if hasattr(eclosion, "lot"):
            raise ValidationError({
                "id_eclosion": (
                    "Un lot existe déjà pour cette éclosion."
                )
            })

        # Revalide les données métier sur l'objet verrouillé.
        if eclosion.statut != Eclosion.Statut.ACTIF:
            raise ValidationError({
                "id_eclosion": (
                    "L'éclosion doit être active pour créer un lot."
                )
            })

        reproduction = eclosion.ponte.reproduction

        serializer.save(
            eclosion=eclosion,
            utilisateur_createur=self.request.user,
            espece=reproduction.espece,
            quantite_initiale=eclosion.quantite_alevins_obtenu,
        )

    @transaction.atomic
    def perform_update(self, serializer):
        lot = self.get_object()

        if lot.stade == Lot.Stade.CLOTUREE:
            raise ValidationError(
                "Un lot clôturé ne peut plus être modifié."
            )

        serializer.save()

    # ROUTE BLOQUÉE (2026-11-17) : PATCH /api/v1/lots/{id}/cloturer/
    #
    # Cette action passait le lot au stade CLOTUREE sans aucun contrôle métier :
    # ni le stade de départ, ni le traitement, la mise en stock ou le lot de
    # grossissement, ni le registre d'effectifs ; sans motif ni trace, et de façon
    # irréversible par l'API. Selon le stade, elle laissait des poissons coincés
    # dans le registre (mise en stock et sortie vers le grossissement refusées),
    # des hapas bloqués à jamais, ou un lot « clôturé » dont le lot de
    # grossissement restait ACTIF (et qu'une annulation de sortie pouvait rouvrir).
    #
    # La seule fin de vie d'un lot est la clôture de son lot de grossissement
    # (grossissement.services.cloturer_lot_grossissement), qui passe le lot
    # d'origine à CLOTUREE. La clôture d'un lot perdu avant le grossissement
    # (effectif nul en traitement ou en stock) reste à concevoir : ne pas
    # réactiver cette action telle quelle.
    #
    # Commentée, l'action n'est plus générée par le routeur : la route répond 404.
    # Les imports `action`, `status` et `Response` ne servent qu'à elle.
    #
    # @action(
    #     detail=True,
    #     methods=["patch"],
    #     url_path="cloturer",
    # )
    # @transaction.atomic
    # def cloturer(self, request, pk=None):
    #     lot = self.get_object()
    #
    #     if lot.stade == Lot.Stade.CLOTUREE:
    #         return Response(
    #             {"detail": "Ce lot est déjà clôturé."},
    #             status=status.HTTP_400_BAD_REQUEST,
    #         )
    #
    #     lot.stade = Lot.Stade.CLOTUREE
    #     lot.save(update_fields=["stade", "updated_at"])
    #
    #     return Response(
    #         {
    #             "detail": "Lot clôturé avec succès.",
    #             "id": lot.id,
    #             "reference": lot.reference,
    #             "stade": lot.stade,
    #         },
    #         status=status.HTTP_200_OK,
    #     )
