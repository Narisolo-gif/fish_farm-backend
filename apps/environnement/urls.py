from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .viewsets import ParametreEnvironnementalViewSet


router = DefaultRouter()

router.register(
    "",
    ParametreEnvironnementalViewSet,
    basename="environnement",
)


urlpatterns = [
    *router.urls,
]