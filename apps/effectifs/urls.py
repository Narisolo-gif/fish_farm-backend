from rest_framework.routers import DefaultRouter

from .viewsets import (
    CategoriePerteViewSet,
    CauseMortaliteViewSet,
    EvenementMortalitePerteViewSet,
    MouvementEffectifViewSet,
)

router = DefaultRouter()
router.register(
    "evenements",
    EvenementMortalitePerteViewSet,
    basename="evenement-mortalite-perte",
)
router.register(
    "causes-mortalite",
    CauseMortaliteViewSet,
    basename="cause-mortalite",
)
router.register(
    "categories-perte",
    CategoriePerteViewSet,
    basename="categorie-perte",
)
# Keep the empty movement prefix last so its detail route cannot capture these endpoints.
router.register("", MouvementEffectifViewSet, basename="mouvement-effectif")

urlpatterns = router.urls
