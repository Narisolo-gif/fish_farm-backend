from rest_framework import serializers

from .models import ParametreEnvironnemental


class ParametreEnvironnementalSerializer(
    serializers.ModelSerializer
):
    """
    Sérialiseur des paramètres environnementaux.
    """

    class Meta:
        model = ParametreEnvironnemental

        fields = [
            "id",
            "bassin",
            "date_heure_prelevement",
            "temperature_eau",
            "temperature_air",
            "oxygene",
            "ph",
            "nitrite",
            "nitrate",
            "ammoniac",
        ]

        read_only_fields = [
            "id",
        ]

    def validate_bassin(self, bassin):
        """
        Vérifie que le bassin est actif.

        On évite d'enregistrer de nouvelles mesures
        sur un bassin déjà mis hors service.
        """

        if bassin.statut != bassin.Statut.ACTIF:
            raise serializers.ValidationError(
                "Impossible d'enregistrer une mesure "
                "pour un bassin inactif."
            )

        return bassin

    def validate_ph(self, value):
        """
        Vérifie que le pH est dans une plage physiquement
        acceptable.
        """

        if value < 0 or value > 14:
            raise serializers.ValidationError(
                "Le pH doit être compris entre 0 et 14."
            )

        return value

    def validate_oxygene(self, value):
        """
        Vérifie que le taux d'oxygène est positif ou nul.
        """

        if value < 0:
            raise serializers.ValidationError(
                "Le taux d'oxygène ne peut pas être négatif."
            )

        return value

    def validate_nitrite(self, value):
        """
        Vérifie que le taux de nitrite est positif ou nul.
        """

        if value < 0:
            raise serializers.ValidationError(
                "Le taux de nitrite ne peut pas être négatif."
            )

        return value

    def validate_nitrate(self, value):
        """
        Vérifie que le taux de nitrate est positif ou nul.
        """

        if value < 0:
            raise serializers.ValidationError(
                "Le taux de nitrate ne peut pas être négatif."
            )

        return value

    def validate_ammoniac(self, value):
        """
        Vérifie que le taux d'ammoniac est positif ou nul.
        """

        if value < 0:
            raise serializers.ValidationError(
                "Le taux d'ammoniac ne peut pas être négatif."
            )

        return value