
from django.utils import timezone

from rest_framework import serializers

from apps.bassins.models import Bassin, Hapa
from apps.lots.models import Lot

from .models import MiseEnStock
from .services import (
    creer_mise_en_stock,
    modifier_mise_en_stock,
)


class MiseEnStockHapaSerializer(serializers.Serializer):
    id_hapa = serializers.PrimaryKeyRelatedField(
        source="hapa",
        queryset=Hapa.objects.all(),
    )
    nom_hapa = serializers.CharField(source="hapa.nom", read_only=True)
    effectif_entrant = serializers.IntegerField(min_value=1)


class MiseEnStockSerializer(serializers.ModelSerializer):
    """Sérialiseur de création, consultation et modification."""

    id_lot = serializers.PrimaryKeyRelatedField(
        source="lot",
        queryset=Lot.objects.all(),
    )

    id_bassin = serializers.PrimaryKeyRelatedField(
        source="bassin",
        queryset=Bassin.objects.all(),
    )
    finalite = serializers.ChoiceField(
        choices=MiseEnStock.Finalite.choices,
        required=False,
    )
    hapas = MiseEnStockHapaSerializer(
        many=True,
        source="lignes_hapas",
        required=False,
    )
    effectif_entrant = serializers.IntegerField(required=False, min_value=1)
    date_mise_en_stock = serializers.DateTimeField(required=False)

    reference_lot = serializers.CharField(
        source="lot.reference",
        read_only=True,
    )

    reference_bassin = serializers.CharField(
        source="bassin.reference",
        read_only=True,
    )

    id_user = serializers.IntegerField(
        source="utilisateur_id",
        read_only=True,
    )

    # L'écart est calculé par le modèle et ne peut pas être envoyé par le client.
    ecart_effectif = serializers.ReadOnlyField()

    class Meta:
        model = MiseEnStock
        fields = [
            "id",
            "id_lot",
            "reference_lot",
            "id_bassin",
            "reference_bassin",
            "finalite",
            "hapas",
            "effectif_entrant",
            "ecart_effectif",
            "date_mise_en_stock",
            "id_user",
            "justification_ecart",
            "observation",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "id_user",
            "ecart_effectif",
            "created_at",
            "updated_at",
        ]

    def validate_effectif_entrant(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "L'effectif entrant doit être strictement supérieur à zéro."
            )
        return value

    def validate_date_mise_en_stock(self, value):
        if timezone.localtime(value).date() > timezone.localdate():
            raise serializers.ValidationError(
                "La date de mise en stock ne peut pas être dans le futur."
            )
        return value

    def validate(self, attrs):
        """Valide le bassin et la justification avant l'enregistrement."""

        if self.instance is None and not attrs.get("finalite"):
            raise serializers.ValidationError({
                "finalite": "La finalité est obligatoire à la création."
            })
        if self.instance is not None and "finalite" in attrs:
            raise serializers.ValidationError({
                "finalite": "La finalité ne peut pas être modifiée."
            })

        lot = attrs.get("lot", getattr(self.instance, "lot", None))
        bassin = attrs.get(
            "bassin",
            getattr(self.instance, "bassin", None),
        )
        hapas = attrs.get("lignes_hapas")
        effectif = (
            sum(ligne["effectif_entrant"] for ligne in hapas)
            if hapas is not None
            else attrs.get(
                "effectif_entrant",
                getattr(self.instance, "effectif_entrant", None),
            )
        )
        justification = attrs.get(
            "justification_ecart",
            getattr(self.instance, "justification_ecart", ""),
        )

        if self.instance is None and not hapas:
            raise serializers.ValidationError({
                "hapas": "Les hapas et leurs effectifs entrants sont obligatoires."
            })

        if bassin:
            if bassin.statut != Bassin.Statut.ACTIF:
                raise serializers.ValidationError({
                    "id_bassin": "Le bassin sélectionné est inactif."
                })

            if bassin.categorie != Bassin.Categorie.STOCKS:
                raise serializers.ValidationError({
                    "id_bassin": (
                        "Le bassin doit être de catégorie STOCKS."
                    )
                })
            if (
                self.instance is None
                and not Hapa.objects.filter(
                    bassin=bassin,
                    actif=True,
                    statut=Hapa.Statut.ACTIF,
                ).exists()
            ):
                raise serializers.ValidationError({
                    "hapas": (
                        "Aucun hapa actif dans le bassin de stock : "
                        "créez d'abord des hapas."
                    )
                })

        if self.instance and lot and lot.pk != self.instance.lot_id:
            raise serializers.ValidationError({
                "id_lot": (
                    "Le lot d'une mise en stock existante ne peut pas être changé."
                )
            })

        if lot and effectif is not None:
            traitement = getattr(lot, "traitement", None)

            # Un traitement commencé doit être clôturé avant le transfert.
            if (
                traitement is not None
                and traitement.statut != traitement.Statut.CLOTUREE
            ):
                raise serializers.ValidationError({
                    "id_lot": (
                        "Le traitement du lot doit être clôturé "
                        "avant sa mise en stock."
                    )
                })
            if (
                traitement is not None
                and not traitement.lignes_hapas.exists()
            ):
                raise serializers.ValidationError({
                    "hapas": (
                        "Traitement sans hapas : supprimez-le et recréez-le."
                    )
                })

            # Pour un lot traité, on compare l'effectif entrant à l'effectif final.
            if traitement is not None and traitement.effectif_final is not None:
                ecart = effectif - traitement.effectif_final

                if ecart != 0 and not justification.strip():
                    raise serializers.ValidationError({
                        "justification_ecart": (
                            "Une justification est obligatoire lorsque "
                            "l'effectif entrant diffère de l'effectif final "
                            "du traitement."
                        )
                    })

            # Sans traitement, on signale aussi un écart à la quantité initiale.
            elif traitement is None:
                ecart_initial = effectif - lot.quantite_initiale

                if ecart_initial != 0 and not justification.strip():
                    raise serializers.ValidationError({
                        "justification_ecart": (
                            "Une justification est obligatoire lorsque "
                            "l'effectif entrant diffère de la quantité "
                            "initiale du lot."
                        )
                    })

        return attrs

    def create(self, validated_data):
        # L'utilisateur provient de la session authentifiée.
        return creer_mise_en_stock(
            validated_data=validated_data,
            utilisateur=self.context["request"].user,
        )

    def update(self, instance, validated_data):
        # Le service vérifie le cycle de vie avant toute modification.
        return modifier_mise_en_stock(
            mise_en_stock_id=instance.pk,
            validated_data=validated_data,
        )
