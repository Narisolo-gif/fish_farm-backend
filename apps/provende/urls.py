from rest_framework.routers import DefaultRouter

from .viewsets import ProvendeViewSet, TypeProvendeViewSet

router = DefaultRouter()

# Déclarer les routes spécifiques avant la route racine.
router.register("types", TypeProvendeViewSet, basename="type-provende")
router.register("", ProvendeViewSet, basename="provende")

urlpatterns = router.urls