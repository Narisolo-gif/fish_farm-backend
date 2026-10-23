from rest_framework.routers import DefaultRouter

from .viewsets import (
    CategoriePerteViewSet,
    CauseMortaliteViewSet,
    EvenementMortalitePerteViewSet,
    MouvementEffectifViewSet,
    SortieStockViewSet,
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
router.register(
    "sorties-stock",
    SortieStockViewSet,
    basename="sortie-stock",
)
# Keep the empty movement prefix last so its detail route cannot capture these endpoints.
router.register("", MouvementEffectifViewSet, basename="mouvement-effectif")

urlpatterns = router.urls
