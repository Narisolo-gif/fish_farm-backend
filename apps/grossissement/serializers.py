from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from .calculs import ecart_poids_pourcentage, taux_survie
from .models import IndicateurLot, LotGrossissement, Pesee
from .services import derniere_pesee_valide, modifier_lot_grossissement


# Le poids moyen initial et la trace de la pesée initiale (P0) ne sont jamais
# modifiables : seule la validation de la sortie les renseigne.
CHAMPS_MODIFIABLES = {"observation", "pourcentage_groupe_pesee"}


class ClotureLotGrossissementSerializer(serializers.Serializer):
    """Confirmation de la clôture : la date, l'effectif et le poids finaux sont
    déterminés par le service ; seule une observation peut être jointe."""

    observation = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )

    def validate(self, attrs):
        inconnus = set(self.initial_data) - {"observation"}
        if inconnus:
            raise serializers.ValidationError({
                champ: "Ce champ n'est pas accepté : la valeur est déterminée à la clôture."
                for champ in sorted(inconnus)
            })
        return attrs


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
    # Suivi : dernière pesée valide (sinon P0), calculé, jamais stocké sur le lot.
    poids_moyen_actuel = serializers.SerializerMethodField()
    date_derniere_pesee = serializers.SerializerMethodField()
    derniers_indicateurs = serializers.SerializerMethodField()
    pesee_en_retard = serializers.BooleanField(read_only=True)
    evacuation_requise = serializers.BooleanField(read_only=True)
    # Sélection du lot dans la page de suivi : date d'entrée et jour de cycle
    # (figé à la clôture ; `age_jours` est conservé pour les clients existants).
    date_entree = serializers.DateField(read_only=True)
    jour_de_cycle = serializers.SerializerMethodField()

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
            "poids_moyen_final",
            "motif_cloture",
            "evacue_le",
            "evacuation_requise",
            "observation",
            "age_jours",
            "date_entree",
            "jour_de_cycle",
            "etape_courante",
            "prochaine_echeance_pesee",
            "pesee_en_retard",
            "poids_moyen_actuel",
            "date_derniere_pesee",
            "derniers_indicateurs",
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

    def _derniere_pesee(self, obj):
        # `pesees_valides` est préchargé par le viewset (triées par date décroissante).
        if hasattr(obj, "pesees_valides"):
            return obj.pesees_valides[0] if obj.pesees_valides else None
        return derniere_pesee_valide(obj)

    def get_poids_moyen_actuel(self, obj):
        derniere = self._derniere_pesee(obj)
        poids = derniere.poids_moyen if derniere is not None else obj.poids_moyen_initial
        return _decimal_ou_none(poids)

    def get_date_derniere_pesee(self, obj):
        derniere = self._derniere_pesee(obj)
        return derniere.date.isoformat() if derniere is not None else None

    def get_derniers_indicateurs(self, obj):
        derniere = self._derniere_pesee(obj)
        return _indicateurs(derniere) if derniere is not None else None

    def get_jour_de_cycle(self, obj):
        return obj.jour_de_cycle()


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

# Saisis par l'utilisateur ; le lot, l'espèce, le bassin, l'effectif vivant,
# les valeurs calculées et l'utilisateur sont renseignés automatiquement.
CHAMPS_PESEE_CREATION = {
    "id_lot_grossissement",
    "date",
    "nombre_poissons_peses",
    "poids_total_g",
    "pourcentage_echantillon",
    "observation",
    "confirmer_echantillon_non_conforme",
}
# La date et le lot d'une pesée sont figés : seules les mesures se corrigent.
CHAMPS_PESEE_MODIFICATION = {
    "nombre_poissons_peses",
    "poids_total_g",
    "pourcentage_echantillon",
    "observation",
    "confirmer_echantillon_non_conforme",
}


def _refuser_champs_inconnus(initial_data, autorises, message):
    inconnus = set(initial_data) - autorises
    if inconnus:
        raise serializers.ValidationError({
            champ: message for champ in sorted(inconnus)
        })


class PeseeEcritureSerializer(serializers.Serializer):
    """Entrée de la création (POST), de l'aperçu et de la correction (PATCH).

    Le caractère obligatoire du nombre et du poids est contrôlé par le
    service (facultatifs à l'aperçu, requis à la création).
    """

    id_lot_grossissement = serializers.IntegerField(min_value=1)
    date = serializers.DateField(required=False)
    nombre_poissons_peses = serializers.IntegerField(required=False)
    poids_total_g = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
    )
    pourcentage_echantillon = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
    )
    observation = serializers.CharField(required=False, allow_blank=True)
    confirmer_echantillon_non_conforme = serializers.BooleanField(
        required=False,
        write_only=True,
    )

    def __init__(self, *args, creation=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.creation = creation
        if creation:
            for champ in ("date", "nombre_poissons_peses", "poids_total_g"):
                self.fields[champ].required = True

    def validate(self, attrs):
        if self.partial:
            _refuser_champs_inconnus(
                self.initial_data,
                CHAMPS_PESEE_MODIFICATION,
                "Ce champ n'est pas modifiable.",
            )
        else:
            _refuser_champs_inconnus(
                self.initial_data,
                CHAMPS_PESEE_CREATION,
                "Ce champ n'est pas accepté.",
            )
        return attrs


class AnnulationPeseeSerializer(serializers.Serializer):
    motif = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )


class IndicateurLotSerializer(serializers.ModelSerializer):
    """Instantané en lecture seule ; les valeurs non calculables valent null.

    `effectif_vivant` est relu dans le registre au dernier recalcul.
    """

    class Meta:
        model = IndicateurLot
        fields = [
            "effectif_vivant",
            "poids_moyen",
            "biomasse_kg",
            "gmq_periode",
            "gmq_cumule",
            "provende_periode_kg",
            "ic_periode",
            "ic_cumule",
            "jour_de_cycle",
        ]
        read_only_fields = fields


def _indicateurs(pesee):
    try:
        indicateur = pesee.indicateur
    except IndicateurLot.DoesNotExist:
        return None
    return IndicateurLotSerializer(indicateur).data


class PeseeSerializer(serializers.ModelSerializer):
    id_lot_grossissement = serializers.IntegerField(
        source="lot_grossissement_id",
        read_only=True,
    )
    reference_lot = serializers.CharField(
        source="lot_grossissement.code",
        read_only=True,
    )
    espece = serializers.CharField(
        source="lot_grossissement.espece",
        read_only=True,
    )
    id_bassin = serializers.IntegerField(source="bassin_id", read_only=True)
    reference_bassin = serializers.CharField(
        source="bassin.reference",
        read_only=True,
    )
    echantillon_conforme = serializers.BooleanField(read_only=True)
    indicateurs = serializers.SerializerMethodField()
    id_user = serializers.IntegerField(source="utilisateur_id", read_only=True)
    id_user_annulation = serializers.IntegerField(
        source="annulee_par_id",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = Pesee
        fields = [
            "id",
            "id_lot_grossissement",
            "reference_lot",
            "espece",
            "id_bassin",
            "reference_bassin",
            "date",
            "effectif_vivant",
            "pourcentage_echantillon",
            "taille_echantillon_cible",
            "nombre_poissons_peses",
            "pourcentage_reel",
            "poids_total_g",
            "poids_moyen",
            "echantillon_conforme",
            "indicateurs",
            "observation",
            "statut",
            "motif_annulation",
            "id_user_annulation",
            "annulee_le",
            "id_user",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_indicateurs(self, obj):
        return _indicateurs(obj)


class ApercuPeseeSerializer(serializers.Serializer):
    """Formulaire de pesée prérempli : valeurs lues et calculées, rien d'écrit."""

    def to_representation(self, contexte):
        lot = contexte["lot"]
        return {
            "id_lot_grossissement": lot.pk,
            "reference_lot": lot.code,
            "espece": lot.espece,
            "id_bassin": lot.bassin_id,
            "reference_bassin": lot.bassin.reference,
            "date": contexte["date"].isoformat(),
            "date_min": contexte["date_min"].isoformat(),
            "date_derniere_pesee": (
                contexte["date_derniere_pesee"].isoformat()
                if contexte["date_derniere_pesee"] is not None
                else None
            ),
            "effectif_vivant": contexte["effectif_vivant"],
            "pourcentage_echantillon": _decimal_ou_none(
                contexte["pourcentage_echantillon"]
            ),
            "taille_echantillon_cible": contexte["taille_echantillon_cible"],
            "nombre_poissons_peses": contexte["nombre_poissons_peses"],
            "pourcentage_reel": _decimal_ou_none(contexte["pourcentage_reel"]),
            "poids_total_g": _decimal_ou_none(contexte["poids_total_g"]),
            "poids_moyen": _decimal_ou_none(contexte["poids_moyen"]),
            "echantillon_conforme": contexte["echantillon_conforme"],
        }


# --- Suivi de cycle ------------------------------------------------------------

def _en_json(valeur):
    """Décimaux en nombres JSON, dates en ISO (dates-heures en heure locale)."""
    if isinstance(valeur, dict):
        return {cle: _en_json(element) for cle, element in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [_en_json(element) for element in valeur]
    if isinstance(valeur, Decimal):
        return float(valeur)
    if isinstance(valeur, datetime):
        return timezone.localtime(valeur).isoformat()
    if isinstance(valeur, date):
        return valeur.isoformat()
    return valeur


class SuiviLotSerializer(serializers.Serializer):
    """Contrat de la page de suivi : toutes les clés toujours présentes."""

    def to_representation(self, contexte):
        return _en_json(contexte)


# --- Clôture --------------------------------------------------------------------

class ApercuClotureSerializer(serializers.Serializer):
    """Résumé en lecture seule du formulaire de clôture."""

    def to_representation(self, contexte):
        lot = contexte["lot"]
        return _en_json({
            "id_lot_grossissement": lot.pk,
            "code": lot.code,
            "espece": lot.espece,
            "statut": lot.statut,
            "date_cloture": contexte["date_cloture"],
            "effectif_initial": contexte["effectif_initial"],
            "effectif_final": contexte["effectif_final"],
            "poids_moyen_initial": contexte["poids_moyen_initial"],
            "poids_moyen_final": contexte["poids_moyen_final"],
            "date_derniere_pesee": contexte["date_derniere_pesee"],
            "seuil_poids_vente_g": contexte["seuil_poids_vente_g"],
            "ecart_poids_pourcentage": contexte["ecart_poids_pourcentage"],
            "cloturable": contexte["cloturable"],
            "raison_refus": contexte["raison_refus"],
        })


class HistoriqueClotureSerializer(serializers.ModelSerializer):
    """Une ligne de l'historique des clôtures ; le détail est dans le suivi du lot."""

    id_bassin = serializers.IntegerField(source="bassin_id", read_only=True)
    reference_bassin = serializers.CharField(source="bassin.reference", read_only=True)
    date_entree = serializers.DateField(read_only=True)
    duree_cycle_jours = serializers.SerializerMethodField()
    taux_survie = serializers.SerializerMethodField()
    ecart_poids_pourcentage = serializers.SerializerMethodField()
    evacue = serializers.SerializerMethodField()

    class Meta:
        model = LotGrossissement
        fields = [
            "id",
            "code",
            "espece",
            "id_bassin",
            "reference_bassin",
            "date_entree",
            "date_cloture",
            "duree_cycle_jours",
            "effectif_initial",
            "effectif_final",
            "taux_survie",
            "poids_moyen_initial",
            "poids_moyen_final",
            "ecart_poids_pourcentage",
            "evacue",
            "evacue_le",
        ]
        read_only_fields = fields

    def get_duree_cycle_jours(self, obj):
        return (obj.date_cloture - obj.date_entree).days

    def get_taux_survie(self, obj):
        return _decimal_ou_none(taux_survie(obj.effectif_initial, obj.effectif_final))

    def get_ecart_poids_pourcentage(self, obj):
        return _decimal_ou_none(
            ecart_poids_pourcentage(obj.poids_moyen_initial, obj.poids_moyen_final)
        )

    def get_evacue(self, obj):
        return not obj.evacuation_requise
