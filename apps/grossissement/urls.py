from rest_framework.routers import DefaultRouter

from .viewsets import LotGrossissementViewSet


router = DefaultRouter()
router.register(
    "lots-grossissement",
    LotGrossissementViewSet,
    basename="lot-grossissement",
)

urlpatterns = router.urls
