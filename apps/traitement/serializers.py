from rest_framework import serializers

from apps.bassins.models import Bassin
from apps.lots.models import Lot
from apps.provende.models import Provende

from .models import Traitement
from .services import creer_traitement


class TraitementSerializer(serializers.ModelSerializer):
    """Sérialiseur de création, consultation et modification du traitement."""

    id_lot = serializers.PrimaryKeyRelatedField(
        source="lot",
        queryset=Lot.objects.all(),
    )

    id_bassin = serializers.PrimaryKeyRelatedField(
        source="bassin",
        queryset=Bassin.objects.all(),
    )

    id_used_provende = serializers.PrimaryKeyRelatedField(
        source="used_provende",
        queryset=Provende.objects.all(),
    )

    # Champs descriptifs en lecture seule.
    reference_lot = serializers.CharField(
        source="lot.reference",
        read_only=True,
    )
    reference_bassin = serializers.CharField(
        source="bassin.reference",
        read_only=True,
    )
    reference_provende = serializers.CharField(
        source="used_provende.reference",
        read_only=True,
    )
    id_user = serializers.IntegerField(
        source="utilisateur_id",
        read_only=True,
    )

    class Meta:
        model = Traitement
        fields = [
            "id",
            "id_lot",
            "reference_lot",
            "effectif_initial",
            "effectif_final",
            "date_ouverture",
            "date_cloture",
            "id_bassin",
            "reference_bassin",
            "id_used_provende",
            "reference_provende",
            "id_user",
            "statut",
            "observation", #Nouvel ajout pour l'observation
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "effectif_final",
            "date_cloture",
            "id_user",
            "statut",
            "created_at",
            "updated_at",
        ]

    def validate_effectif_initial(self, value):
        """Vérifie que l'effectif initial respecte celui du lot associé."""
        # Un effectif initial nul ou négatif n'est pas autorisé.
        if value <= 0:
            raise serializers.ValidationError(
                "L'effectif initial doit être strictement supérieur à zéro."
            )

        # En création, le lot est transmis dans les données validées.
        # En modification, on récupère le lot de l'instance existante.
        lot = self.initial_data.get("id_lot")

        if self.instance is not None:
            lot_obj = self.instance.lot
        elif lot is not None:
            from apps.lots.models import Lot

            try:
                lot_obj = Lot.objects.get(pk=lot)
            except (Lot.DoesNotExist, ValueError, TypeError):
                raise serializers.ValidationError(
                    "Le lot indiqué est invalide."
                )
        else:
            # La validation globale du serializer traitera l'absence du lot.
            return value

        # Le traitement ne peut pas contenir plus de poissons que le lot.
        if value > lot_obj.quantite_initiale:
            raise serializers.ValidationError(
                "L'effectif initial du traitement ne peut pas dépasser "
                "l'effectif initial du lot "
                f"({lot_obj.quantite_initiale})."
            )

        return value
    
    def validate(self, attrs):
        """Applique les règles métier communes à la création et à la mise à jour."""

        bassin = attrs.get(
            "bassin",
            getattr(self.instance, "bassin", None),
        )
        lot = attrs.get(
            "lot",
            getattr(self.instance, "lot", None),
        )

        if bassin:
            if bassin.statut != Bassin.Statut.ACTIF:
                raise serializers.ValidationError({
                    "id_bassin": "Le bassin sélectionné est inactif."
                })

            if bassin.categorie != Bassin.Categorie.TRAITEMENT:
                raise serializers.ValidationError({
                    "id_bassin": (
                        "Le bassin doit être de catégorie TRAITEMENT."
                    )
                })

        if self.instance:
            # Un traitement clôturé devient non modifiable.
            if self.instance.statut == Traitement.Statut.CLOTUREE:
                raise serializers.ValidationError(
                    "Un traitement clôturé ne peut plus être modifié."
                )

            # Le lot associé ne peut pas être remplacé.
            if "lot" in attrs and attrs["lot"].pk != self.instance.lot_id:
                raise serializers.ValidationError({
                    "id_lot": (
                        "Le lot d'un traitement existant ne peut pas être changé."
                    )
                })

        elif lot and Traitement.objects.filter(lot=lot).exists():
            # Première vérification avant l'appel au service.
            raise serializers.ValidationError({
                "id_lot": "Ce lot possède déjà un traitement."
            })

        return attrs

    def create(self, validated_data):
        """Délègue la création au service transactionnel."""

        # Le créateur vient de la requête authentifiée, jamais du JSON client.
        utilisateur = self.context["request"].user

        return creer_traitement(
            validated_data=validated_data,
            utilisateur=utilisateur,
        )


class ClotureTraitementSerializer(serializers.Serializer):
    """Entrée dédiée à l'action de clôture."""

    effectif_final = serializers.IntegerField(min_value=0)

    def validate_effectif_final(self, value):
        traitement = self.context["traitement"]

        if value > traitement.effectif_initial:
            raise serializers.ValidationError(
                "L'effectif final ne peut pas dépasser l'effectif initial."
            )

        return value