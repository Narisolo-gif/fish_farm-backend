
from rest_framework import status, viewsets
from rest_framework.response import Response

from apps.core.permissions import PermissionMatrice

from .models import MiseEnStock
from .serializers import MiseEnStockSerializer
from .services import supprimer_mise_en_stock


class MiseEnStockViewSet(viewsets.ModelViewSet):
    """API CRUD des mises en stock."""

    ressource = "stocks"
    permission_classes = [PermissionMatrice]

    serializer_class = MiseEnStockSerializer

    queryset = MiseEnStock.objects.select_related(
        "lot",
        "bassin",
        "utilisateur",
        "lot__traitement",
    ).prefetch_related(
        "lignes_hapas__hapa",
    ).order_by(
        "-date_mise_en_stock",
        "-id",
    )

    def perform_destroy(self, instance):
        # Le service sécurise la suppression et la transition du lot.
        supprimer_mise_en_stock(instance.pk)
