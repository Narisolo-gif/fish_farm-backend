from rest_framework import serializers

from .models import LotGrossissement
from .services import modifier_lot_grossissement


class ClotureLotGrossissementSerializer(serializers.Serializer):
    motif = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )


class LotGrossissementSerializer(serializers.ModelSerializer):
    id_lot_origine = serializers.IntegerField(
        source="lot_origine_id",
        read_only=True,
    )
    reference_lot_origine = serializers.CharField(
        source="lot_origine.reference",
        read_only=True,
    )
    id_sortie_stock = serializers.IntegerField(
        source="sortie_stock_id",
        read_only=True,
    )
    id_bassin = serializers.IntegerField(source="bassin_id", read_only=True)
    reference_bassin = serializers.CharField(
        source="bassin.reference",
        read_only=True,
    )
    id_user = serializers.IntegerField(source="utilisateur_id", read_only=True)
    age_jours = serializers.IntegerField(read_only=True)
    etape_courante = serializers.IntegerField(read_only=True, allow_null=True)
    prochaine_echeance_pesee = serializers.DateField(read_only=True, allow_null=True)

    class Meta:
        model = LotGrossissement
        fields = [
            "id",
            "code",
            "id_lot_origine",
            "reference_lot_origine",
            "id_sortie_stock",
            "id_bassin",
            "reference_bassin",
            "espece",
            "date_mise_en_place",
            "effectif_initial",
            "poids_initial_g",
            "pourcentage_groupe_pesee",
            "statut",
            "date_cloture",
            "effectif_final",
            "poids_final_g",
            "motif_cloture",
            "observation",
            "age_jours",
            "etape_courante",
            "prochaine_echeance_pesee",
            "id_user",
            "created_at",
        ]
        read_only_fields = [
            field for field in fields
            if field not in {"observation", "pourcentage_groupe_pesee"}
        ]
        extra_kwargs = {
            "observation": {"required": False, "allow_blank": True},
            "pourcentage_groupe_pesee": {"required": False},
        }

    def validate_pourcentage_groupe_pesee(self, value):
        if value <= 0 or value > 100:
            raise serializers.ValidationError(
                "Le pourcentage doit être supérieur à 0 et inférieur ou égal à 100."
            )
        return value

    def validate(self, attrs):
        champs_modifiables = {"observation", "pourcentage_groupe_pesee"}
        champs_interdits = set(self.initial_data) - champs_modifiables
        if champs_interdits:
            raise serializers.ValidationError({
                champ: "Ce champ est en lecture seule."
                for champ in sorted(champs_interdits)
            })
        return attrs

    def update(self, instance, validated_data):
        return modifier_lot_grossissement(instance.pk, validated_data)


class OrigineLotGrossissementSerializer(serializers.Serializer):
    def to_representation(self, instance):
        lot = instance.lot_origine
        traitement = getattr(lot, "traitement", None)
        mise_en_stock = getattr(lot, "mise_en_stock", None)

        traitement_data = None
        if traitement is not None:
            traitement_data = {
                "date_ouverture": traitement.date_ouverture,
                "date_cloture": traitement.date_cloture,
                "effectif_initial": traitement.effectif_initial,
                "effectif_final": traitement.effectif_final,
                "bassin": traitement.bassin.reference,
                "hapas": [
                    {
                        "nom_hapa": ligne.hapa.nom,
                        "effectif_initial": ligne.effectif_initial,
                        "effectif_final": ligne.effectif_final,
                    }
                    for ligne in traitement.lignes_hapas.all()
                ],
            }

        mise_en_stock_data = None
        if mise_en_stock is not None:
            mise_en_stock_data = {
                "date": mise_en_stock.date_mise_en_stock,
                "finalite": mise_en_stock.finalite,
                "effectif_entrant": mise_en_stock.effectif_entrant,
                "ecart_effectif": mise_en_stock.ecart_effectif,
                "justification_ecart": mise_en_stock.justification_ecart,
                "bassin": mise_en_stock.bassin.reference,
                "hapas": [
                    {
                        "nom_hapa": ligne.hapa.nom,
                        "effectif_entrant": ligne.effectif_entrant,
                    }
                    for ligne in mise_en_stock.lignes_hapas.all()
                ],
            }

        sortie_stock = instance.sortie_stock
        return {
            "lot_origine": {
                "reference": lot.reference,
                "espece": lot.espece,
            },
            "traitement": traitement_data,
            "mise_en_stock": mise_en_stock_data,
            "sortie_stock": {
                "date_sortie": sortie_stock.date_sortie,
                "effectif_sortant": sortie_stock.effectif_sortant,
                "effectif_entrant": sortie_stock.effectif_entrant,
                "ecart_effectif": sortie_stock.ecart_effectif,
                "justification_ecart": sortie_stock.justification_ecart,
            },
        }