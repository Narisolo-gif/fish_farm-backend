from rest_framework.routers import DefaultRouter

from .viewsets import LotGrossissementViewSet, PeseeViewSet


router = DefaultRouter()
router.register(
    "lots-grossissement",
    LotGrossissementViewSet,
    basename="lot-grossissement",
)

router.register("pesees", PeseeViewSet, basename="pesee")

urlpatterns = router.urls
