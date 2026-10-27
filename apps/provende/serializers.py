from rest_framework import serializers
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.bassins.models import Bassin, Hapa

from .models import ConsommationProvende, Provende, TypeProvende
from .services import (
    creer_provende,
    creer_type_provende,
    enregistrer_distribution,
)


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


class ConsommationProvendeSerializer(serializers.ModelSerializer):
    id_bassin = serializers.PrimaryKeyRelatedField(
        source="bassin",
        queryset=Bassin.objects.all(),
    )
    reference_bassin = serializers.CharField(
        source="bassin.reference",
        read_only=True,
    )
    id_hapa = serializers.PrimaryKeyRelatedField(
        source="hapa",
        queryset=Hapa.objects.all(),
        required=False,
        allow_null=True,
    )
    nom_hapa = serializers.CharField(source="hapa.nom", read_only=True, allow_null=True)
    id_provende = serializers.PrimaryKeyRelatedField(
        source="provende",
        queryset=Provende.objects.select_related("type_provende").all(),
    )
    reference_provende = serializers.CharField(
        source="provende.reference",
        read_only=True,
    )
    type_provende = serializers.CharField(
        source="provende.type_provende.get_type_display",
        read_only=True,
    )
    id_user = serializers.IntegerField(
        source="utilisateur_id",
        read_only=True,
    )
    annulee_par = serializers.IntegerField(
        source="annulee_par_id",
        read_only=True,
    )
    # `None` passe au service, qui applique l'heure courante puis la normalise.
    date_distribution = serializers.DateTimeField(
        required=False,
        default=None,
    )

    class Meta:
        model = ConsommationProvende
        fields = [
            "id",
            "id_bassin",
            "reference_bassin",
            "id_hapa",
            "nom_hapa",
            "id_provende",
            "reference_provende",
            "type_provende",
            "date_distribution",
            "quantite_kg",
            "observation",
            "statut",
            "motif_annulation",
            "annulee_par",
            "annulee_le",
            "id_user",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "reference_bassin",
            "reference_provende",
            "type_provende",
            "statut",
            "motif_annulation",
            "annulee_par",
            "annulee_le",
            "id_user",
            "created_at",
        ]
        extra_kwargs = {
            "quantite_kg": {"min_value": 0},
            "observation": {"required": False, "allow_blank": True},
        }

    def validate_date_distribution(self, value):
        if value is not None and timezone.is_naive(value):
            raise ValidationError(
                "Fournissez une date ISO 8601 avec fuseau horaire."
            )
        return value

    def create(self, validated_data):
        return enregistrer_distribution(
            validated_data,
            utilisateur=self.context["request"].user,
        )


class AnnulationConsommationSerializer(serializers.Serializer):
    motif = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )