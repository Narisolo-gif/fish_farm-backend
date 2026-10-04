from django.db import transaction
from rest_framework import serializers

from .models import Bassin, Hapa


class HapaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Hapa
        fields = [
            "id",
            "reference",
            "bassin",
            "capacity",
            "statut",
        ]
        read_only_fields = [
            "id",
            "bassin",
        ]


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