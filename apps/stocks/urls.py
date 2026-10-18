from rest_framework.routers import DefaultRouter

from .viewsets import MiseEnStockViewSet

router = DefaultRouter()
router.register("", MiseEnStockViewSet, basename="mise-en-stock")

urlpatterns = router.urls
