from django.contrib import admin

from .models import ConsommationProvende


@admin.register(ConsommationProvende)
class ConsommationProvendeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "bassin",
        "provende",
        "date_distribution",
        "quantite_kg",
        "statut",
        "utilisateur",
    )
    list_filter = ("statut", "bassin", "provende__type_provende")
    search_fields = ("bassin__reference", "provende__reference", "observation")
    readonly_fields = [field.name for field in ConsommationProvende._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
