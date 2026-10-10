from rest_framework.routers import DefaultRouter

from .viewsets import LotViewSet

router = DefaultRouter()
router.register(r"", LotViewSet, basename="lot")

urlpatterns = router.urls
