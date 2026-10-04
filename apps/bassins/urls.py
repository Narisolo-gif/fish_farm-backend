from django.urls import path
from rest_framework.routers import DefaultRouter

from .viewsets import BassinViewSet, HapaViewSet


router = DefaultRouter()

# Routes principales des bassins :
#
# GET    /api/v1/bassins/
# POST   /api/v1/bassins/
# GET    /api/v1/bassins/{id}/
# PUT    /api/v1/bassins/{id}/
# PATCH  /api/v1/bassins/{id}/
# DELETE /api/v1/bassins/{id}/
router.register(
    "",
    BassinViewSet,
    basename="bassin",
)


urlpatterns = [
    # Routes générées automatiquement pour les bassins.
    *router.urls,

    # Routes des hapas rattachés à un bassin.
    #
    # GET  /api/v1/bassins/{bassin_id}/hapas/
    # POST /api/v1/bassins/{bassin_id}/hapas/
    path(
        "<int:bassin_id>/hapas/",
        HapaViewSet.as_view({
            "get": "list",
            "post": "create",
        }),
        name="bassin-hapas",
    ),

    # Routes d'un hapa appartenant à un bassin.
    #
    # GET    /api/v1/bassins/{bassin_id}/hapas/{pk}/
    # PUT    /api/v1/bassins/{bassin_id}/hapas/{pk}/
    # PATCH  /api/v1/bassins/{bassin_id}/hapas/{pk}/
    # DELETE /api/v1/bassins/{bassin_id}/hapas/{pk}/
    path(
        "<int:bassin_id>/hapas/<int:pk>/",
        HapaViewSet.as_view({
            "get": "retrieve",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }),
        name="bassin-hapa-detail",
    ),
]