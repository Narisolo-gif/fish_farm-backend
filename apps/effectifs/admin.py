from django.contrib import admin

from .models import MouvementEffectif


@admin.register(MouvementEffectif)
class MouvementEffectifAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "bassin",
        "espece",
        "type_mouvement",
        "sens",
        "quantite",
        "date_evenement",
        "saisi_par",
        "annule_mouvement",
    )
    list_filter = (
        "bassin",
        "espece",
        "type_mouvement",
        "date_evenement",
    )
    search_fields = (
        "espece",
        "motif_annulation",
        "bassin__reference",
    )
    readonly_fields = [
        field.name for field in MouvementEffectif._meta.fields
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
