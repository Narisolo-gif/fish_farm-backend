from rest_framework import serializers

from apps.bassins.models import Bassin, Hapa
from apps.lots.models import Lot

from .models import (
    CategoriePerte,
    CauseMortalite,
    EvenementMortalitePerte,
    MouvementEffectif,
)
from .services import enregistrer_evenement_mortalite_perte


class MouvementEffectifSerializer(serializers.ModelSerializer):
    reference_bassin = serializers.CharField(
        source="bassin.reference",
        read_only=True,
    )
    reference_hapa = serializers.CharField(
        source="hapa.reference",
        read_only=True,
    )
    reference_lot_origine = serializers.CharField(
        source="lot_origine.reference",
        read_only=True,
    )
    reference_lot_grossissement = serializers.CharField(
        source="lot_grossissement.reference",
        read_only=True,
    )
    saisi_par_username = serializers.CharField(
        source="saisi_par.username",
        read_only=True,
    )

    class Meta:
        model = MouvementEffectif
        fields = [
            "id",
            "bassin",
            "reference_bassin",
            "espece",
            "type_mouvement",
            "sens",
            "quantite",
            "date_evenement",
            "lot_origine",
            "reference_lot_origine",
            "hapa",
            "reference_hapa",
            "lot_grossissement",
            "reference_lot_grossissement",
            "annule_mouvement",
            "motif_annulation",
            "operation_source_content_type",
            "operation_source_object_id",
            "saisi_par",
            "saisi_par_username",
            "saisi_le",
        ]
        # L'API expose les mouvements en lecture seule ; les créations passent par les opérations métier.
        read_only_fields = fields


class AnnulationMouvementSerializer(serializers.Serializer):
    motif_annulation = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    date_annulation = serializers.DateField(
        required=False,
        allow_null=False,
    )

    def validate_date_annulation(self, value):
        from django.utils import timezone

        if value > timezone.localdate():
            raise serializers.ValidationError(
                "La date d’annulation ne peut pas être dans le futur."
            )
        return value


class CauseMortaliteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CauseMortalite
        fields = ["id", "libelle", "actif"]
        read_only_fields = fields


class CategoriePerteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriePerte
        fields = ["id", "libelle", "actif"]
        read_only_fields = fields


class EvenementMortalitePerteSerializer(serializers.ModelSerializer):
    bassin = serializers.PrimaryKeyRelatedField(queryset=Bassin.objects.all())
    cause_mortalite = serializers.PrimaryKeyRelatedField(
        queryset=CauseMortalite.objects.filter(actif=True),
        required=False,
        allow_null=True,
    )
    categorie_perte = serializers.PrimaryKeyRelatedField(
        queryset=CategoriePerte.objects.filter(actif=True),
        required=False,
        allow_null=True,
    )
    mouvement = serializers.PrimaryKeyRelatedField(read_only=True)
    statut = serializers.CharField(read_only=True)
    saisi_par = serializers.PrimaryKeyRelatedField(read_only=True)
    saisi_le = serializers.DateTimeField(read_only=True)

    class Meta:
        model = EvenementMortalitePerte
        fields = [
            "id",
            "bassin",
            "espece",
            "type_evenement",
            "quantite",
            "date_evenement",
            "cause_mortalite",
            "categorie_perte",
            "justification",
            "mouvement",
            "statut",
            "saisi_par",
            "saisi_le",
        ]
        read_only_fields = [
            "id",
            "mouvement",
            "statut",
            "saisi_par",
            "saisi_le",
        ]

    def validate(self, attrs):
        event_type = attrs.get("type_evenement")
        cause = attrs.get("cause_mortalite")
        category = attrs.get("categorie_perte")
        justification = attrs.get("justification", "")

        if event_type == EvenementMortalitePerte.TypeEvenement.MORTALITE:
            if cause is None:
                raise serializers.ValidationError({
                    "cause_mortalite": "Une cause de mortalité est obligatoire."
                })
            if category is not None:
                raise serializers.ValidationError({
                    "categorie_perte": "Une mortalité ne peut pas avoir de catégorie de perte."
                })
        elif event_type == EvenementMortalitePerte.TypeEvenement.PERTE:
            if category is None:
                raise serializers.ValidationError({
                    "categorie_perte": "Une catégorie de perte est obligatoire."
                })
            if cause is not None:
                raise serializers.ValidationError({
                    "cause_mortalite": "Une perte ne peut pas avoir de cause de mortalité."
                })
            if not justification.strip():
                raise serializers.ValidationError({
                    "justification": "La justification est obligatoire pour une perte."
                })
        return attrs

    def create(self, validated_data):
        return enregistrer_evenement_mortalite_perte(
            **validated_data,
            saisi_par=self.context["request"].user,
        )


class AnnulationEvenementSerializer(serializers.Serializer):
    motif_annulation = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
