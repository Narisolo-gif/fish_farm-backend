from django.contrib import admin

from .models import (
    CategoriePerte,
    CauseMortalite,
    EvenementMortalitePerte,
    MouvementEffectif,
)


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

    # Les corrections passent par une annulation, jamais par l'édition ou la suppression admin.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CauseMortalite)
class CauseMortaliteAdmin(admin.ModelAdmin):
    list_display = ("libelle", "actif")
    list_filter = ("actif",)
    search_fields = ("libelle",)


@admin.register(CategoriePerte)
class CategoriePerteAdmin(admin.ModelAdmin):
    list_display = ("libelle", "actif")
    list_filter = ("actif",)
    search_fields = ("libelle",)


@admin.register(EvenementMortalitePerte)
class EvenementMortalitePerteAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "bassin",
        "espece",
        "type_evenement",
        "quantite",
        "date_evenement",
        "statut",
        "saisi_par",
    )
    list_filter = (
        "bassin",
        "espece",
        "type_evenement",
        "statut",
        "date_evenement",
        "cause_mortalite",
        "categorie_perte",
    )
    search_fields = ("justification", "bassin__reference")
    readonly_fields = [field.name for field in EvenementMortalitePerte._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
