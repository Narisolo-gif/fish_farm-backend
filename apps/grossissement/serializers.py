from rest_framework import serializers

from .models import IndicateurLot, LotGrossissement, Pesee, PeseeLot
from .services import modifier_lot_grossissement


# Le poids moyen initial et la trace de la pesée initiale (P0) ne sont jamais
# modifiables : seule la validation de la sortie les renseigne.
CHAMPS_MODIFIABLES = {"observation", "pourcentage_groupe_pesee"}


class ClotureLotGrossissementSerializer(serializers.Serializer):
    motif = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    poids_final_g = serializers.DecimalField(
        max_digits=8,
        decimal_places=2,
        required=True,
    )
    date_cloture = serializers.DateField(required=False)

    def validate_poids_final_g(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Le poids final doit être strictement positif."
            )
        return value


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
    effectif_sortant = serializers.IntegerField(
        source="sortie_stock.effectif_sortant",
        read_only=True,
        allow_null=True,
    )
    ecart_effectif = serializers.IntegerField(
        source="sortie_stock.ecart_effectif",
        read_only=True,
        allow_null=True,
    )
    justification_ecart = serializers.CharField(
        source="sortie_stock.justification_ecart",
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
            "effectif_sortant",
            "effectif_initial",
            "ecart_effectif",
            "justification_ecart",
            "poids_moyen_initial",
            "date_pesee_initiale",
            "pourcentage_echantillon_initial",
            "nombre_poissons_peses_initial",
            "poids_total_initial_g",
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
            if field not in CHAMPS_MODIFIABLES
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
        champs_interdits = set(self.initial_data) - CHAMPS_MODIFIABLES
        if champs_interdits:
            raise serializers.ValidationError({
                champ: "Ce champ est en lecture seule."
                for champ in sorted(champs_interdits)
            })
        return attrs

    def update(self, instance, validated_data):
        return modifier_lot_grossissement(instance.pk, validated_data)


CHAMPS_SORTIE_GROSSISSEMENT = {
    "id_lot",
    "id_bassin_destination",
    "date_pesee_initiale",
    "pourcentage_echantillon",
    "nombre_poissons_peses",
    "poids_total_g",
    "confirmer_echantillon_non_conforme",
    "code",
    "date_mise_en_place",
    "effectif_entrant",
    "justification_ecart",
    "observation",
}


class SortieGrossissementSerializer(serializers.Serializer):
    """Entrée unique de l'aperçu et de la validation de la sortie vers le grossissement.

    Le caractère obligatoire des champs de la pesée initiale et du lot est
    contrôlé par le service (facultatifs à l'aperçu, requis à la validation).
    """

    # La sortie
    id_lot = serializers.IntegerField(min_value=1)
    id_bassin_destination = serializers.IntegerField(min_value=1)
    # La pesée initiale (P0)
    date_pesee_initiale = serializers.DateField(required=False)
    pourcentage_echantillon = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
    )
    nombre_poissons_peses = serializers.IntegerField(required=False)
    poids_total_g = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
    )
    confirmer_echantillon_non_conforme = serializers.BooleanField(required=False)
    # Le lot
    code = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    date_mise_en_place = serializers.DateField(required=False)
    effectif_entrant = serializers.IntegerField(required=False)
    justification_ecart = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    observation = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        # Une seule voie d'entrée : le poids moyen initial est calculé depuis P0 ;
        # tout autre champ (dont l'ancien poids_initial_g) est refusé.
        inconnus = set(self.initial_data) - CHAMPS_SORTIE_GROSSISSEMENT
        if inconnus:
            raise serializers.ValidationError({
                champ: "Ce champ n'est pas accepté."
                for champ in sorted(inconnus)
            })
        return attrs


def _decimal_ou_none(valeur, decimales=2):
    if valeur is None:
        return None
    return f"{valeur:.{decimales}f}"


class ApercuSortieGrossissementSerializer(serializers.Serializer):
    """Représentation de l'aperçu : valeurs préremplies et calculées, rien d'écrit."""

    def to_representation(self, contexte):
        lot = contexte["lot"]
        preparation = contexte["preparation"]
        bassin_source = preparation["bassin_source"]
        bassin = contexte["bassin"]
        dates = {
            champ: (
                contexte[champ].isoformat()
                if contexte[champ] is not None
                else None
            )
            for champ in ("date_mise_en_place", "date_pesee_initiale")
        }
        effectif_entrant = contexte["effectif_entrant"]
        return {
            "id_lot": lot.pk,
            "reference_lot_origine": lot.reference,
            "espece": lot.espece,
            "id_bassin_source": bassin_source.pk,
            "reference_bassin_source": bassin_source.reference,
            "id_bassin_destination": bassin.pk,
            "reference_bassin_destination": bassin.reference,
            "lignes": [
                {
                    "id_hapa": ligne["hapa"].pk,
                    "nom_hapa": ligne["hapa"].nom,
                    "quantite": ligne["quantite"],
                }
                for ligne in preparation["lignes"]
            ],
            "quantite_sortie": contexte["quantite_sortie"],
            "code_propose": contexte["code_propose"],
            "code": contexte["code"],
            **dates,
            "pourcentage_echantillon": _decimal_ou_none(
                contexte["pourcentage_echantillon"]
            ),
            "taille_echantillon_cible": contexte["taille_echantillon_cible"],
            "nombre_poissons_peses": contexte["nombre_poissons_peses"],
            "poids_total_g": _decimal_ou_none(contexte["poids_total_g"]),
            "poids_moyen_initial": _decimal_ou_none(
                contexte["poids_moyen_initial"]
            ),
            "echantillon_conforme": contexte["echantillon_conforme"],
            "effectif_entrant": effectif_entrant,
            "ecart_effectif": contexte["ecart_effectif"],
            "justification_requise": (
                effectif_entrant is not None
                and effectif_entrant != contexte["quantite_sortie"]
            ),
        }


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

# --- Pesée sur échantillon -------------------------------------------------

CHAMPS_PESEE_CREATION = {
    "id_bassin",
    "date",
    "observation",
    "confirmer_echantillon_non_conforme",
    "lignes",
}
CHAMPS_PESEE_MODIFICATION = {
    "observation",
    "confirmer_echantillon_non_conforme",
    "lignes",
}
CHAMPS_LIGNE_PESEE = {
    "id_lot_grossissement",
    "pourcentage_echantillon",
    "nombre_poissons_peses",
    "poids_total_g",
}


class LignePeseeEcritureSerializer(serializers.Serializer):
    id_lot_grossissement = serializers.IntegerField(min_value=1)
    pourcentage_echantillon = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
    )
    nombre_poissons_peses = serializers.IntegerField()
    poids_total_g = serializers.DecimalField(max_digits=12, decimal_places=2)

    def validate_pourcentage_echantillon(self, value):
        if value <= 0 or value > 100:
            raise serializers.ValidationError(
                "Le pourcentage doit être supérieur à 0 et inférieur ou égal à 100."
            )
        return value

    def validate_nombre_poissons_peses(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Le nombre de poissons pesés doit être strictement positif."
            )
        return value

    def validate_poids_total_g(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Le poids total doit être strictement positif."
            )
        return value


class PeseeEcritureSerializer(serializers.Serializer):
    """Entrée de création (POST) et de modification limitée (PATCH)."""

    id_bassin = serializers.IntegerField(min_value=1)
    date = serializers.DateField()
    observation = serializers.CharField(required=False, allow_blank=True)
    confirmer_echantillon_non_conforme = serializers.BooleanField(
        required=False,
        write_only=True,
    )
    lignes = LignePeseeEcritureSerializer(many=True)

    def validate(self, attrs):
        autorises = (
            CHAMPS_PESEE_MODIFICATION if self.partial else CHAMPS_PESEE_CREATION
        )
        interdits = set(self.initial_data) - autorises
        if interdits:
            raise serializers.ValidationError({
                champ: "Ce champ est en lecture seule."
                for champ in sorted(interdits)
            })
        for ligne in self.initial_data.get("lignes") or []:
            interdits_ligne = set(ligne) - CHAMPS_LIGNE_PESEE
            if interdits_ligne:
                raise serializers.ValidationError({
                    "lignes": (
                        "Champs en lecture seule : "
                        f"{', '.join(sorted(interdits_ligne))}."
                    )
                })
        if self.partial:
            for ligne in attrs.get("lignes") or []:
                if "id_lot_grossissement" not in ligne:
                    raise serializers.ValidationError({
                        "lignes": "Chaque ligne modifiée doit indiquer id_lot_grossissement."
                    })
        return attrs


class IndicateurLotSerializer(serializers.ModelSerializer):
    """Instantané en lecture seule ; les valeurs non calculables valent null."""

    class Meta:
        model = IndicateurLot
        fields = [
            "biomasse_kg",
            "gmq_periode",
            "gmq_cumule",
            "provende_periode_kg",
            "ic_periode",
            "ic_cumule",
            "jour_de_cycle",
        ]
        read_only_fields = fields


class PeseeLotSerializer(serializers.ModelSerializer):
    id_lot_grossissement = serializers.IntegerField(
        source="lot_grossissement_id",
        read_only=True,
    )
    reference_lot = serializers.CharField(
        source="lot_grossissement.code",
        read_only=True,
    )
    echantillon_conforme = serializers.BooleanField(read_only=True)
    indicateurs = serializers.SerializerMethodField()

    class Meta:
        model = PeseeLot
        fields = [
            "id",
            "id_lot_grossissement",
            "reference_lot",
            "pourcentage_echantillon",
            "effectif_vivant",
            "taille_echantillon_cible",
            "nombre_poissons_peses",
            "poids_total_g",
            "poids_moyen",
            "echantillon_conforme",
            "indicateurs",
        ]
        read_only_fields = fields

    def get_indicateurs(self, obj):
        try:
            indicateur = obj.indicateur
        except IndicateurLot.DoesNotExist:
            return None
        return IndicateurLotSerializer(indicateur).data


class PeseeSerializer(serializers.ModelSerializer):
    id_bassin = serializers.IntegerField(source="bassin_id", read_only=True)
    reference_bassin = serializers.CharField(
        source="bassin.reference",
        read_only=True,
    )
    id_user = serializers.IntegerField(source="utilisateur_id", read_only=True)
    lignes = PeseeLotSerializer(many=True, read_only=True)

    class Meta:
        model = Pesee
        fields = [
            "id",
            "id_bassin",
            "reference_bassin",
            "date",
            "observation",
            "id_user",
            "lignes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
