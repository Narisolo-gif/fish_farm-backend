from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
	RoleViewSet,
	UtilisateurViewSet,
	logout,
	me,
)

router = DefaultRouter()

router.register("roles", RoleViewSet, basename="role")
router.register("users", UtilisateurViewSet, basename="user")

urlpatterns = [
	path("auth/me/", me, name="me"),
 	path("auth/logout/", logout, name="logout"),
	*router.urls,
]