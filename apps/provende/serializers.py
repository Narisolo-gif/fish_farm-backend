from rest_framework import serializers

from .models import Provende, TypeProvende
from .services import creer_provende, creer_type_provende


class TypeProvendeSerializer(serializers.ModelSerializer):
    """Sérialiseur pour la consultation et la gestion des types."""

    class Meta:
        model = TypeProvende
        fields = ["id", "type"]
        read_only_fields = ["id"]

    def create(self, validated_data):
        # La création passe par le service métier.
        return creer_type_provende(validated_data)


class ProvendeSerializer(serializers.ModelSerializer):
    """Sérialiseur des provendes."""

    # Expose un libellé lisible en plus de l'identifiant du type.
    type_provende_libelle = serializers.CharField(
        source="type_provende.get_type_display",
        read_only=True,
    )

    class Meta:
        model = Provende
        fields = [
            "id",
            "type_provende",
            "type_provende_libelle",
            "reference",
            "date_creation",
            "remarques",
        ]
        read_only_fields = ["id", "type_provende_libelle"]

    def validate_reference(self, value):
        """Vérifie l'unicité de la référence, même en modification."""

        value = value.strip()

        doublons = Provende.objects.filter(reference=value)

        # Exclut l'enregistrement courant lors d'une mise à jour.
        if self.instance:
            doublons = doublons.exclude(pk=self.instance.pk)

        if doublons.exists():
            raise serializers.ValidationError(
                "Cette référence de provende existe déjà."
            )

        return value

    def create(self, validated_data):
        # Centralise la création dans services.py.
        return creer_provende(validated_data)