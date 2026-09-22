from rest_framework.routers import DefaultRouter

from .views import RoleViewSet, UtilisateurViewSet


router = DefaultRouter()

router.register("roles", RoleViewSet, basename="role")
router.register("users", UtilisateurViewSet, basename="user")

urlpatterns = router.urls