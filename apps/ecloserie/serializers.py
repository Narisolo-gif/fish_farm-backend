
from django.utils import timezone
from rest_framework import serializers

from apps.reproduction.models import Ponte
from .models import Eclosion


class EclosionSerializer(serializers.ModelSerializer):
    reference_ponte = serializers.SlugRelatedField(
        source="ponte",
        slug_field="reference",
        queryset=Ponte.objects.all(),
    )

    class Meta:
        model = Eclosion
        fields = [
            "id",
            "reference",
            "reference_ponte",
            "date_eclosion",
            "quantite_oeufs_eclos",
            "quantite_alevins_obtenu",
            "observation",
            "statut",
        ]
        read_only_fields = ["id", "statut"]

    def validate_date_eclosion(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError(
                "La date d'éclosion ne peut pas être dans le futur."
            )
        return value

    def validate_quantite_oeufs_eclos(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError(
                "La quantité d'œufs éclos doit être supérieure à 0."
            )
        return value

    def validate_quantite_alevins_obtenu(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "La quantité d'alevins obtenus doit être supérieure à 0."
            )
        return value
