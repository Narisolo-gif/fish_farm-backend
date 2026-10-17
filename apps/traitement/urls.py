from rest_framework.routers import DefaultRouter

from .viewsets import TraitementViewSet

router = DefaultRouter()

# Le préfixe vide expose /api/v1/traitement/ grâce à l'inclusion principale.
router.register("", TraitementViewSet, basename="traitement")

urlpatterns = router.urls