"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path("api/", include("apps.core.urls")),
    path("api/v1/auth/", include("apps.core.urls")),
    path("api/v1/bassins/", include("apps.bassins.urls")),
    path("api/v1/environnement/", include("apps.environnement.urls")),
    path("api/v1/provende/", include("apps.provende.urls")),
    path("api/v1/lots/", include("apps.lots.urls")),
    path("api/v1/reproduction/", include("apps.reproduction.urls")),
    path("api/v1/ecloserie/", include("apps.ecloserie.urls")),
    path("api/v1/traitement/", include("apps.traitement.urls")),
    path("api/v1/stocks/", include("apps.stocks.urls")),
    path("api/v1/grossissement/", include("apps.grossissement.urls")),
    path("api/v1/monitoring/", include("apps.monitoring.urls")),
]
