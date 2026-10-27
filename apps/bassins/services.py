from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import Bassin, Hapa


@transaction.atomic
def creer_hapa(validated_data):
    donnees = dict(validated_data)
    bassin = Bassin.objects.select_for_update().get(pk=donnees["bassin"].pk)
    if bassin.categorie != Bassin.Categorie.STOCKS:
        raise ValidationError({
            "bassin": "Le bassin du hapa doit être de catégorie STOCKS."
        })
    donnees["bassin"] = bassin
    if "actif" in donnees:
        donnees["statut"] = (
            Hapa.Statut.ACTIF if donnees["actif"] else Hapa.Statut.INACTIF
        )
    elif "statut" in donnees:
        donnees["actif"] = donnees["statut"] == Hapa.Statut.ACTIF
    return Hapa.objects.create(**donnees)


@transaction.atomic
def modifier_hapa(hapa_id, validated_data):
    hapa = Hapa.objects.select_for_update().get(pk=hapa_id)
    if "bassin" in validated_data and validated_data["bassin"].pk != hapa.bassin_id:
        raise ValidationError({
            "bassin": "Le bassin de rattachement d’un hapa ne peut pas être modifié."
        })
    donnees = dict(validated_data)
    donnees.pop("bassin", None)
    if "actif" in donnees:
        donnees["statut"] = (
            Hapa.Statut.ACTIF if donnees["actif"] else Hapa.Statut.INACTIF
        )
    elif "statut" in donnees:
        donnees["actif"] = donnees["statut"] == Hapa.Statut.ACTIF
    for champ, valeur in donnees.items():
        setattr(hapa, champ, valeur)
    hapa.save()
    return hapa