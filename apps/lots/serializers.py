
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
            "parcours_production",
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

        # Le parcours est défini à la création et ne change plus ensuite.
        if instance and "parcours_production" in attrs:
            if (
                attrs["parcours_production"]
                != instance.parcours_production
            ):
                raise serializers.ValidationError({
                    "parcours_production": (
                        "Le parcours de production ne peut pas être "
                        "modifié après la création du lot."
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
                        "L'éclosion doit être active pour créer un lot."
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

        if date_creation is not None and timezone.is_naive(date_creation):
            attrs["date_creation"] = timezone.make_aware(
                date_creation,
                timezone.get_current_timezone(),
            )

        return attrs

    def create(self, validated_data):
        # Les données biologiques proviennent de l'éclosion liée.
        eclosion = validated_data["eclosion"]
        reproduction = eclosion.ponte.reproduction

        validated_data["espece"] = reproduction.espece
        validated_data["quantite_initiale"] = (
            eclosion.quantite_alevins_obtenu
        )

        # Si le client omet le parcours, le défaut du modèle s'applique.
        return super().create(validated_data)
