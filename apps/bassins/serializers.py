from django.db import transaction
from rest_framework import serializers

from .models import Bassin, Hapa
from .services import creer_hapa, modifier_hapa


class HapaSerializer(serializers.ModelSerializer):
    nom = serializers.CharField(required=False, allow_blank=True, max_length=100)
    actif = serializers.BooleanField(required=False)

    class Meta:
        model = Hapa
        fields = [
            "id",
            "reference",
            "bassin",
            "capacity",
            "statut",
            "nom",
            "actif",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "bassin",
            "created_at",
        ]

    def validate(self, attrs):
        reference = attrs.get("reference", getattr(self.instance, "reference", None))
        if not attrs.get("nom") and reference:
            attrs["nom"] = reference
        if "actif" in attrs:
            attrs["statut"] = (
                Hapa.Statut.ACTIF if attrs["actif"] else Hapa.Statut.INACTIF
            )
        elif "statut" in attrs:
            attrs["actif"] = attrs["statut"] == Hapa.Statut.ACTIF
        return attrs

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation["actif"] = (
            instance.actif and instance.statut == Hapa.Statut.ACTIF
        )
        return representation

    def create(self, validated_data):
        return creer_hapa(validated_data)

    def update(self, instance, validated_data):
        return modifier_hapa(instance.pk, validated_data)


class HapaGestionSerializer(serializers.ModelSerializer):
    bassin = serializers.PrimaryKeyRelatedField(
        queryset=Bassin.objects.all(),
    )

    class Meta:
        model = Hapa
        fields = ["id", "bassin", "nom", "actif", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_bassin(self, bassin):
        return bassin

    def validate(self, attrs):
        if (
            self.instance
            and "bassin" in attrs
            and attrs["bassin"].pk != self.instance.bassin_id
        ):
            raise serializers.ValidationError({
                "bassin": "Le bassin de rattachement d’un hapa ne peut pas être modifié."
            })
        if "actif" in attrs:
            attrs["statut"] = (
                Hapa.Statut.ACTIF if attrs["actif"] else Hapa.Statut.INACTIF
            )
        return attrs

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation["actif"] = (
            instance.actif and instance.statut == Hapa.Statut.ACTIF
        )
        return representation

    def create(self, validated_data):
        return creer_hapa(validated_data)

    def update(self, instance, validated_data):
        return modifier_hapa(instance.pk, validated_data)


class BassinSerializer(serializers.ModelSerializer):
    hapas = HapaSerializer(
        many=True,
        required=False,
    )

    class Meta:
        model = Bassin
        fields = [
            "id",
            "reference",
            "categorie",
            "capacity",
            "statut",
            "hapas",
        ]
        read_only_fields = [
            "id",
        ]

    @transaction.atomic
    def create(self, validated_data):
        hapas_data = validated_data.pop("hapas", [])

        bassin = Bassin.objects.create(**validated_data)

        Hapa.objects.bulk_create(
            [
                Hapa(
                    bassin=bassin,
                    **hapa_data,
                )
                for hapa_data in hapas_data
            ]
        )

        return bassin

    def update(self, instance, validated_data):
        validated_data.pop("hapas", None)

        return super().update(
            instance,
            validated_data,
        )