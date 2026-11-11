from django.utils import timezone
from rest_framework import serializers

from apps.bassins.models import Bassin, Hapa
from apps.lots.models import Lot

from .models import (
    CategoriePerte,
    CauseMortalite,
    EvenementMortalitePerte,
    MouvementEffectif,
    SortieStock,
    SortieStockLigne,
)
from .services import (
    calculer_lignes_sortie,
    creer_sortie,
    enregistrer_evenement,
    modifier_sortie,
)


class MouvementEffectifSerializer(serializers.ModelSerializer):
    reference_bassin = serializers.CharField(
        source="bassin.reference",
        read_only=True,
    )
    reference_hapa = serializers.CharField(
        source="hapa.reference",
        read_only=True,
        allow_null=True,
    )
    id_hapa = serializers.IntegerField(source="hapa_id", read_only=True, allow_null=True)
    nom_hapa = serializers.CharField(source="hapa.nom", read_only=True, allow_null=True)
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
            "id_hapa",
            "nom_hapa",
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
        # L'API expose les mouvements en lecture seule ; les créations passent par les opérations métier.
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


class CauseMortaliteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CauseMortalite
        fields = ["id", "libelle", "actif"]


class CategoriePerteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriePerte
        fields = ["id", "libelle", "actif"]


class SortieStockLigneSerializer(serializers.ModelSerializer):
    id_hapa = serializers.IntegerField(source="hapa_id", read_only=True)
    nom_hapa = serializers.CharField(source="hapa.nom", read_only=True)

    class Meta:
        model = SortieStockLigne
        fields = ["id_hapa", "nom_hapa", "quantite"]
        read_only_fields = fields


class SortieStockSerializer(serializers.ModelSerializer):
    id_lot = serializers.PrimaryKeyRelatedField(
        source="lot",
        queryset=Lot.objects.all(),
    )
    reference_lot = serializers.CharField(source="lot.reference", read_only=True)
    id_bassin_destination = serializers.PrimaryKeyRelatedField(
        source="bassin_destination",
        queryset=Bassin.objects.all(),
    )
    reference_bassin_source = serializers.SerializerMethodField()
    id_bassin_source = serializers.SerializerMethodField()
    reference_bassin_destination = serializers.CharField(
        source="bassin_destination.reference",
        read_only=True,
    )
    id_user = serializers.IntegerField(source="utilisateur_id", read_only=True)
    date_sortie = serializers.DateTimeField(required=False)
    ecart_effectif = serializers.IntegerField(read_only=True, allow_null=True)
    lignes = SortieStockLigneSerializer(many=True, read_only=True)

    class Meta:
        model = SortieStock
        fields = [
            "id",
            "type_sortie",
            "id_lot",
            "reference_lot",
            "espece",
            "id_bassin_source",
            "id_bassin_destination",
            "reference_bassin_source",
            "reference_bassin_destination",
            "date_sortie",
            "statut",
            "observation",
            "effectif_sortant",
            "effectif_entrant",
            "ecart_effectif",
            "justification_ecart",
            "lignes",
            "id_user",
            "validee_le",
            "annulee_le",
            "motif_annulation",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "type_sortie",
            "reference_lot",
            "espece",
            "id_bassin_source",
            "reference_bassin_source",
            "reference_bassin_destination",
            "statut",
            "effectif_sortant",
            "effectif_entrant",
            "ecart_effectif",
            "justification_ecart",
            "lignes",
            "id_user",
            "validee_le",
            "annulee_le",
            "motif_annulation",
            "created_at",
        ]
        extra_kwargs = {
            "observation": {"required": False, "allow_blank": True},
        }

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None:
            fields["id_lot"].read_only = True
        return fields

    def _hapa_source(self, instance):
        ligne = instance.lignes.select_related("hapa", "hapa__bassin").first()
        if ligne is not None:
            return ligne.hapa
        # Brouillon : la source est recalculée. Si le lot n'est plus sortable
        # (hapa vidé, inactif, partagé…), on renvoie null au lieu de faire
        # échouer toute la liste ; l'erreur reste levée à l'aperçu et à la validation.
        try:
            lignes = calculer_lignes_sortie(
                instance.lot,
                timezone.localtime(instance.date_sortie).date(),
            )
        except serializers.ValidationError:
            return None
        return lignes[0]["hapa"] if lignes else None

    def get_id_bassin_source(self, instance):
        hapa = self._hapa_source(instance)
        return hapa.bassin_id if hapa is not None else None

    def get_reference_bassin_source(self, instance):
        hapa = self._hapa_source(instance)
        return hapa.bassin.reference if hapa is not None else None

    def create(self, validated_data):
        return creer_sortie(
            validated_data,
            utilisateur=self.context["request"].user,
        )

    def update(self, instance, validated_data):
        return modifier_sortie(instance.pk, validated_data)


class ValidationSortieSerializer(serializers.Serializer):
    effectif_entrant = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
    )
    justification_ecart = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )


class AnnulationSortieStockSerializer(serializers.Serializer):
    motif = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )


class EvenementMortalitePerteSerializer(serializers.ModelSerializer):
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
    id_cause = serializers.PrimaryKeyRelatedField(
        source="cause_mortalite",
        queryset=CauseMortalite.objects.filter(actif=True),
        required=False,
        allow_null=True,
    )
    libelle_cause = serializers.CharField(
        source="cause_mortalite.libelle",
        read_only=True,
        allow_null=True,
    )
    id_categorie = serializers.PrimaryKeyRelatedField(
        source="categorie_perte",
        queryset=CategoriePerte.objects.filter(actif=True),
        required=False,
        allow_null=True,
    )
    libelle_categorie = serializers.CharField(
        source="categorie_perte.libelle",
        read_only=True,
        allow_null=True,
    )
    id_user = serializers.IntegerField(source="utilisateur_id", read_only=True)
    statut = serializers.CharField(read_only=True)
    motif_annulation = serializers.CharField(read_only=True)
    annulee_le = serializers.DateTimeField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    date_evenement = serializers.DateTimeField(required=False)

    class Meta:
        model = EvenementMortalitePerte
        fields = [
            "id",
            "type_evenement",
            "id_hapa",
            "id_bassin",
            "reference_bassin",
            "nom_hapa",
            "espece",
            "quantite",
            "date_evenement",
            "id_cause",
            "libelle_cause",
            "id_categorie",
            "libelle_categorie",
            "justification",
            "observation",
            "statut",
            "motif_annulation",
            "annulee_le",
            "id_user",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "reference_bassin",
            "nom_hapa",
            "libelle_cause",
            "libelle_categorie",
            "statut",
            "motif_annulation",
            "annulee_le",
            "id_user",
            "created_at",
        ]

    def create(self, validated_data):
        return enregistrer_evenement(
            validated_data=validated_data,
            utilisateur=self.context["request"].user,
        )


class AnnulationEvenementSerializer(serializers.Serializer):
    motif = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
