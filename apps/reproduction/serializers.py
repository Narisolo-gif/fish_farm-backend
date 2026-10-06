from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers

from apps.bassins.models import Bassin

from .models import Ponte, Reproduction


class ReproductionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reproduction
        fields = [
            "id",
            "reference",
            "bassin",
            "espece",
            "date_ajout",
            "quantite_male",
            "quantite_femelle",
            "observation",
            "statut",
        ]
        read_only_fields = [
            "id",
            "statut",
        ]

    def validate(self, attrs):
        instance = self.instance

        if instance and instance.statut == Reproduction.Statut.INACTIF:
            raise serializers.ValidationError(
                "Une reproduction inactive ne peut plus être modifiée."
            )

        bassin = attrs.get(
            "bassin",
            instance.bassin if instance else None,
        )

        if bassin is None:
            raise serializers.ValidationError(
                {"bassin": "Le bassin est obligatoire."}
            )

        if instance and "bassin" in attrs:
            if attrs["bassin"].id != instance.bassin_id:
                raise serializers.ValidationError(
                    {
                        "bassin": (
                            "Le bassin d'une reproduction existante "
                            "ne peut pas être modifié."
                        )
                    }
                )

        if bassin.statut != Bassin.Statut.ACTIF:
            raise serializers.ValidationError(
                {
                    "bassin": (
                        "Impossible d'enregistrer une reproduction "
                        "dans un bassin inactif."
                    )
                }
            )

        date_ajout = attrs.get(
            "date_ajout",
            instance.date_ajout if instance else None,
        )

        if date_ajout > timezone.localdate():
            raise serializers.ValidationError(
                {
                    "date_ajout": (
                        "La date d'ajout ne peut pas être dans le futur."
                    )
                }
            )

        quantite_male = attrs.get(
            "quantite_male",
            instance.quantite_male if instance else None,
        )

        quantite_femelle = attrs.get(
            "quantite_femelle",
            instance.quantite_femelle if instance else None,
        )

        if quantite_male < 1:
            raise serializers.ValidationError(
                {
                    "quantite_male": (
                        "La quantité de mâles doit être supérieure ou égale à 1."
                    )
                }
            )

        if quantite_femelle < 1:
            raise serializers.ValidationError(
                {
                    "quantite_femelle": (
                        "La quantité de femelles doit être supérieure ou égale à 1."
                    )
                }
            )

        quantite_totale = quantite_male + quantite_femelle

        if quantite_totale > bassin.capacity:
            raise serializers.ValidationError(
                {
                    "quantite_male": (
                        "La quantité totale de poissons dépasse "
                        "la capacité du bassin."
                    )
                }
            )

        # Lors d'une création, le bassin ne doit pas déjà avoir
        # une reproduction active.
        if instance is None:
            if Reproduction.objects.filter(
                bassin=bassin,
                statut=Reproduction.Statut.ACTIF,
            ).exists():
                raise serializers.ValidationError(
                    {
                        "bassin": (
                            "Ce bassin possède déjà une reproduction active."
                        )
                    }
                )

        return attrs


class PonteSerializer(serializers.ModelSerializer):
    bassin = serializers.PrimaryKeyRelatedField(
        queryset=Bassin.objects.all(),
        write_only=True,
    )

    reproduction = serializers.PrimaryKeyRelatedField(
        read_only=True,
    )

    class Meta:
        model = Ponte
        fields = [
            "id",
            "reference",
            "bassin",
            "reproduction",
            "date_ponte",
            "quantite_oeuf_obtenu",
            "observation",
        ]
        read_only_fields = [
            "id",
            "reproduction",
        ]

    def validate(self, attrs):
        instance = self.instance

        # --------------------------------------------------
        # Détermination du bassin
        # --------------------------------------------------
        bassin = attrs.get("bassin")

        if instance:
            # Une ponte existante ne peut pas être déplacée
            # vers un autre bassin.
            if bassin is not None:
                if bassin.id != instance.reproduction.bassin_id:
                    raise serializers.ValidationError(
                        {
                            "bassin": (
                                "Le bassin d'une ponte existante "
                                "ne peut pas être modifié."
                            )
                        }
                    )

            bassin = instance.reproduction.bassin

        # --------------------------------------------------
        # Bassin actif obligatoire
        # --------------------------------------------------
        if bassin.statut != Bassin.Statut.ACTIF:
            raise serializers.ValidationError(
                {
                    "bassin": (
                        "Impossible d'enregistrer une ponte "
                        "dans un bassin inactif."
                    )
                }
            )

        # --------------------------------------------------
        # Date de ponte
        # --------------------------------------------------
        date_ponte = attrs.get(
            "date_ponte",
            instance.date_ponte if instance else None,
        )

        if date_ponte > timezone.localdate():
            raise serializers.ValidationError(
                {
                    "date_ponte": (
                        "La date de ponte ne peut pas "
                        "être dans le futur."
                    )
                }
            )

        # --------------------------------------------------
        # Quantité d'œufs
        # --------------------------------------------------
        quantite_oeuf = attrs.get(
            "quantite_oeuf_obtenu",
            instance.quantite_oeuf_obtenu if instance else None,
        )

        if quantite_oeuf <= 0:
            raise serializers.ValidationError(
                {
                    "quantite_oeuf_obtenu": (
                        "La quantité d'œufs doit être "
                        "strictement supérieure à 0."
                    )
                }
            )

        return attrs

    def create(self, validated_data):
        """
        Crée une ponte à partir du bassin fourni par le frontend.

        Le frontend ne fournit jamais l'identifiant de la reproduction.
        Le backend recherche automatiquement la reproduction active
        correspondant au bassin.
        """
        bassin = validated_data.pop("bassin")

        reproduction = (
            Reproduction.objects
            .filter(
                bassin=bassin,
                statut=Reproduction.Statut.ACTIF,
            )
            .first()
        )

        if reproduction is None:
            raise serializers.ValidationError(
                {
                    "bassin": (
                        "Ce bassin ne possède aucune "
                        "reproduction active."
                    )
                }
            )

        return Ponte.objects.create(
            reproduction=reproduction,
            **validated_data,
        )

    def update(self, instance, validated_data):
        """
        Modifie une ponte sans permettre de changer son bassin
        ou sa reproduction.
        """
        validated_data.pop("bassin", None)

        return super().update(
            instance,
            validated_data,
        )