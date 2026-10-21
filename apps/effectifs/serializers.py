from rest_framework import serializers

from apps.bassins.models import Bassin, Hapa
from apps.lots.models import Lot

from .models import MouvementEffectif


class MouvementEffectifSerializer(serializers.ModelSerializer):
    reference_bassin = serializers.CharField(
        source="bassin.reference",
        read_only=True,
    )
    reference_hapa = serializers.CharField(
        source="hapa.reference",
        read_only=True,
    )
    reference_lot_origine = serializers.CharField(
        source="lot_origine.reference",
        read_only=True,
    )
    reference_lot_grossissement = serializers.CharField(
        source="lot_grossissement.reference",
        read_only=True,
    )
    saisi_par_username = serializers.CharField(
        source="saisi_par.username",
        read_only=True,
    )

    class Meta:
        model = MouvementEffectif
        fields = [
            "id",
            "bassin",
            "reference_bassin",
            "espece",
            "type_mouvement",
            "sens",
            "quantite",
            "date_evenement",
            "lot_origine",
            "reference_lot_origine",
            "hapa",
            "reference_hapa",
            "lot_grossissement",
            "reference_lot_grossissement",
            "annule_mouvement",
            "motif_annulation",
            "operation_source_content_type",
            "operation_source_object_id",
            "saisi_par",
            "saisi_par_username",
            "saisi_le",
        ]
        read_only_fields = fields


class AnnulationMouvementSerializer(serializers.Serializer):
    motif_annulation = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    date_annulation = serializers.DateField(
        required=False,
        allow_null=False,
    )

    def validate_date_annulation(self, value):
        from django.utils import timezone

        if value > timezone.localdate():
            raise serializers.ValidationError(
                "La date d’annulation ne peut pas être dans le futur."
            )
        return value
