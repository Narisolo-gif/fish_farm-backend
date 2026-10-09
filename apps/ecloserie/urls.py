from rest_framework.routers import DefaultRouter

from .viewsets import EclosionViewSet

router = DefaultRouter()
router.register(
    r"",
    EclosionViewSet,
    basename="eclosion",
)

urlpatterns = router.urls
