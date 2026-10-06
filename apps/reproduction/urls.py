from rest_framework.routers import DefaultRouter

from .viewsets import (
    PonteViewSet,
    ReproductionViewSet,
)

router = DefaultRouter()

router.register(
    r"reproductions",
    ReproductionViewSet,
    basename="reproduction",
)

router.register(
    r"pontes",
    PonteViewSet,
    basename="ponte",
)

urlpatterns = router.urls