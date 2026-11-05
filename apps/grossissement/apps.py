from django.apps import AppConfig


class GrossissementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.grossissement"

    def ready(self):
        from . import receivers  # noqa: F401
