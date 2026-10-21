from rest_framework.routers import DefaultRouter

from .viewsets import MouvementEffectifViewSet

router = DefaultRouter()
router.register("", MouvementEffectifViewSet, basename="mouvement-effectif")

urlpatterns = router.urls
