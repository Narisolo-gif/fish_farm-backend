
from django.utils import timezone
from rest_framework import serializers

from apps.ecloserie.models import Eclosion
from apps.reproduction.models import Reproduction

from .models import Lot


class LotSerializer(serializers.ModelSerializer):
    id_eclosion = serializers.PrimaryKeyRelatedField(
        source="eclosion",
        queryset=Eclosion.objects.select_related(
            "ponte",
            "ponte__reproduction",
        ).all(),
    )

    reference_eclosion = serializers.CharField(
        source="eclosion.reference",
        read_only=True,
    )

    espece = serializers.CharField(read_only=True)
    quantite_initiale = serializers.IntegerField(read_only=True)

    id_utilisateur_createur = serializers.IntegerField(
        source="utilisateur_createur_id",
        read_only=True,
    )

    class Meta:
        model = Lot
        fields = [
            "id",
            "reference",
            "id_eclosion",
            "reference_eclosion",
            "date_creation",
            "espece",
            "id_utilisateur_createur",
            "quantite_initiale",
            "stade",
            "observations",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "espece",
            "id_utilisateur_createur",
            "quantite_initiale",
            "stade",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        instance = self.instance

        if instance and instance.stade == Lot.Stade.CLOTUREE:
            raise serializers.ValidationError(
                "Un lot clôturé ne peut plus être modifié."
            )

        if instance and "eclosion" in attrs:
            if attrs["eclosion"].pk != instance.eclosion_id:
                raise serializers.ValidationError({
                    "id_eclosion": (
                        "L'éclosion associée à un lot "
                        "ne peut pas être modifiée."
                    )
                })

        eclosion = attrs.get(
            "eclosion",
            instance.eclosion if instance else None,
        )

        if eclosion is not None:
            if eclosion.statut != Eclosion.Statut.ACTIF:
                raise serializers.ValidationError({
                    "id_eclosion": (
                        "L'éclosion doit être active pour "
                        "créer un lot."
                    )
                })

            reproduction = eclosion.ponte.reproduction

            if reproduction.espece not in dict(
                Reproduction.Espece.choices
            ):
                raise serializers.ValidationError({
                    "id_eclosion": (
                        "L'espèce de la reproduction associée "
                        "n'est pas valide."
                    )
                })

        date_creation = attrs.get(
            "date_creation",
            instance.date_creation if instance else None,
        )

        if date_creation is not None and timezone.is_naive(
            date_creation
        ):
            attrs["date_creation"] = timezone.make_aware(
                date_creation,
                timezone.get_current_timezone(),
            )

        return attrs

    def create(self, validated_data):
        # Récupère l'éclosion sélectionnée par l'utilisateur.
        eclosion = validated_data["eclosion"]

        # Remonte de l'éclosion vers la ponte, puis vers la reproduction.
        reproduction = eclosion.ponte.reproduction

        # L'espèce du lot provient de la reproduction existante.
        validated_data["espece"] = reproduction.espece

        # La quantité initiale est celle des alevins obtenus à l'éclosion.
        validated_data["quantite_initiale"] = (
            eclosion.quantite_alevins_obtenu
        )

        # Crée le lot avec les données calculées et celles validées.
        return super().create(validated_data)