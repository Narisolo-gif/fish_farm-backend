from django.db.models import Prefetch
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.permissions import PermissionMatrice
from apps.lots.models import Lot

from .models import LotGrossissement, Pesee
from .serializers import (
    ApercuSortieGrossissementSerializer,
    ClotureLotGrossissementSerializer,
    HistoriqueClotureSerializer,
    LotGrossissementSerializer,
    OrigineLotGrossissementSerializer,
    AnnulationPeseeSerializer,
    ApercuClotureSerializer,
    ApercuPeseeSerializer,
    PeseeEcritureSerializer,
    PeseeSerializer,
    SortieGrossissementSerializer,
    SuiviLotSerializer,
)
from .services import (
    EchantillonNonConforme,
    annuler_pesee,
    apercu_pesee,
    apercu_sortie_grossissement,
    cloturer_lot_grossissement,
    creer_pesee,
    evacuer_lot_grossissement,
    modifier_pesee,
    preparer_cloture,
    valider_sortie_grossissement,
)
from .suivi import suivi_lot


class LotGrossissementViewSet(viewsets.ModelViewSet):
    """
    Lots de grossissement.

    POST / : sortie totale du stock et création du lot, en une seule transaction.
    POST /apercu-sortie/ : mêmes contrôles, valeurs préremplies, rien n'est écrit.
    Droits : matrice `lots_grossissement` (apps/core/permissions.py) : tous les
    rôles consultent ; Administrateur et Directeur écrivent (sortie, clôture,
    évacuation, modification).
    """

    ressource = "lots_grossissement"
    permission_classes = [PermissionMatrice]

    queryset = (
        LotGrossissement.objects.select_related(
            "lot_origine",
            "sortie_stock",
            "bassin",
            "utilisateur",
            "lot_origine__traitement__bassin",
            "lot_origine__mise_en_stock__bassin",
        )
        .prefetch_related(
            "lot_origine__traitement__lignes_hapas__hapa",
            "lot_origine__mise_en_stock__lignes_hapas__hapa",
            # Dernière pesée valide et ses indicateurs (fiche du lot).
            Prefetch(
                "pesees",
                queryset=Pesee.objects.filter(statut=Pesee.Statut.VALIDEE)
                .select_related("indicateur")
                .order_by("-date", "-id"),
                to_attr="pesees_valides",
            ),
        )
        .all()
    )
    serializer_class = LotGrossissementSerializer
    http_method_names = ["get", "patch", "post", "head", "options"]

    def create(self, request, *args, **kwargs):
        serializer = SortieGrossissementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            lot = valider_sortie_grossissement(
                serializer.validated_data,
                utilisateur=request.user,
            )
        except EchantillonNonConforme as exc:
            return _reponse_echantillon_non_conforme(exc)
        lot = self.queryset.get(pk=lot.pk)
        return Response(
            self.get_serializer(lot).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"], url_path="apercu-sortie")
    def apercu_sortie(self, request):
        serializer = SortieGrossissementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contexte = apercu_sortie_grossissement(serializer.validated_data)
        return Response(ApercuSortieGrossissementSerializer(contexte).data)

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        for parametre, champ in (
            ("statut", "statut"),
            ("espece", "espece"),
        ):
            valeur = params.get(parametre)
            if valeur:
                options = (
                    LotGrossissement.Statut.values
                    if champ == "statut"
                    else Lot.Espece.values
                )
                if valeur not in options:
                    raise ValidationError({parametre: "Valeur inconnue."})
                queryset = queryset.filter(**{champ: valeur})

        for parametre, champ in (
            ("bassin", "bassin_id"),
            ("lot_origine", "lot_origine_id"),
        ):
            valeur = params.get(parametre)
            if valeur:
                if not valeur.isdigit():
                    raise ValidationError({parametre: "Identifiant invalide."})
                queryset = queryset.filter(**{champ: valeur})

        for parametre, lookup in (
            ("date_min", "date_mise_en_place__date__gte"),
            ("date_max", "date_mise_en_place__date__lte"),
        ):
            valeur = params.get(parametre)
            date = parse_date(valeur) if valeur else None
            if valeur and date is None:
                raise ValidationError({
                    parametre: "Format invalide. Utilisez YYYY-MM-DD."
                })
            if date:
                queryset = queryset.filter(**{lookup: date})

        date_min = parse_date(params.get("date_min", ""))
        date_max = parse_date(params.get("date_max", ""))
        if date_min and date_max and date_max < date_min:
            raise ValidationError({
                "date_max": "La date de fin doit être égale ou postérieure au début."
            })
        return queryset.order_by("-created_at", "-id")

    @action(detail=True, methods=["get"], url_path="apercu-cloture")
    def apercu_cloture(self, request, pk=None):
        """Formulaire de clôture en lecture seule ; rien n'est écrit."""
        contexte = preparer_cloture(self.get_object())
        return Response(ApercuClotureSerializer(contexte).data)

    @action(detail=True, methods=["post"], url_path="cloturer")
    def cloturer(self, request, pk=None):
        serializer = ClotureLotGrossissementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lot = cloturer_lot_grossissement(
            self.get_object().pk,
            utilisateur=request.user,
            observation=serializer.validated_data.get("observation", ""),
        )
        lot = self.queryset.get(pk=lot.pk)
        return Response({
            **self.get_serializer(lot).data,
            "message": f"Le cycle du lot {lot.code} a bien été clôturé.",
        })

    @action(detail=True, methods=["post"], url_path="evacuer")
    def evacuer(self, request, pk=None):
        """Évacuation du bassin, demandée après la clôture (transaction à part)."""
        lot = evacuer_lot_grossissement(self.get_object().pk, utilisateur=request.user)
        lot = self.queryset.get(pk=lot.pk)
        return Response({
            **self.get_serializer(lot).data,
            "message": f"Le bassin du lot {lot.code} a bien été évacué.",
        })

    @action(detail=False, methods=["get"], url_path="historique-clotures")
    def historique_clotures(self, request):
        """Lots clôturés, du plus récent au plus ancien."""
        lots = LotGrossissement.objects.select_related("bassin").filter(
            statut=LotGrossissement.Statut.CLOTURE
        )
        params = request.query_params
        espece = params.get("espece")
        if espece:
            if espece not in Lot.Espece.values:
                raise ValidationError({"espece": "Valeur inconnue."})
            lots = lots.filter(espece=espece)
        bassin = params.get("bassin")
        if bassin:
            if not bassin.isdigit():
                raise ValidationError({"bassin": "Identifiant invalide."})
            lots = lots.filter(bassin_id=bassin)
        bornes = {}
        for parametre, lookup in (
            ("date_min", "date_cloture__gte"),
            ("date_max", "date_cloture__lte"),
        ):
            valeur = params.get(parametre)
            if not valeur:
                continue
            try:
                jour = parse_date(valeur)
            except ValueError:
                jour = None
            if jour is None:
                raise ValidationError({parametre: "Format invalide. Utilisez YYYY-MM-DD."})
            bornes[parametre] = jour
            lots = lots.filter(**{lookup: jour})
        if len(bornes) == 2 and bornes["date_max"] < bornes["date_min"]:
            raise ValidationError({
                "date_max": "La date de fin doit être égale ou postérieure au début."
            })
        lots = lots.order_by("-date_cloture", "-id")
        return Response(HistoriqueClotureSerializer(lots, many=True).data)

    @action(detail=True, methods=["get"], url_path="suivi")
    def suivi(self, request, pk=None):
        """Suivi de cycle du lot à `date_reference` (YYYY-MM-DD, défaut : aujourd'hui)."""
        valeur = request.query_params.get("date_reference")
        date_reference = timezone.localdate()
        if valeur:
            try:
                date_reference = parse_date(valeur)
            except ValueError:
                date_reference = None
            if date_reference is None:
                raise ValidationError({
                    "date_reference": "Format invalide. Utilisez YYYY-MM-DD."
                })
        lot = self.get_object()
        return Response(SuiviLotSerializer(suivi_lot(lot, date_reference)).data)

    @action(detail=True, methods=["get"], url_path="origine")
    def origine(self, request, pk=None):
        lot = self.get_object()
        serializer = OrigineLotGrossissementSerializer(lot)
        return Response(serializer.data)

def _reponse_echantillon_non_conforme(exc):
    return Response(
        {
            "echantillon_non_conforme": exc.message,
            "lignes_non_conformes": exc.lignes_non_conformes,
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


class PeseeViewSet(viewsets.ModelViewSet):
    """
    Pesées sur échantillon, une par lot de grossissement et par date.

    POST / : enregistre une pesée (lot, espèce, bassin et effectif déduits).
    POST /apercu/ : formulaire prérempli et valeurs calculées, rien n'est écrit.
    PATCH /{id}/ : corrige la dernière pesée valide du lot (date figée).
    POST /{id}/annuler/ : annule la dernière pesée valide (motif obligatoire).
    Aucune suppression : DELETE n'est pas permis.

    Droits : matrice `pesees` (apps/core/permissions.py) : tous les rôles
    consultent ; l'Employé saisit (création, aperçu) ; Administrateur et
    Directeur saisissent, corrigent et annulent.
    """

    ressource = "pesees"
    permission_classes = [PermissionMatrice]

    queryset = Pesee.objects.select_related(
        "lot_grossissement",
        "bassin",
        "indicateur",
    ).all()
    serializer_class = PeseeSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        for parametre, champ in (
            ("bassin", "bassin_id"),
            ("lot", "lot_grossissement_id"),
        ):
            valeur = params.get(parametre)
            if valeur:
                if not valeur.isdigit():
                    raise ValidationError({parametre: "Identifiant invalide."})
                queryset = queryset.filter(**{champ: valeur})

        statut_pesee = params.get("statut")
        if statut_pesee:
            if statut_pesee not in Pesee.Statut.values:
                raise ValidationError({"statut": "Valeur inconnue."})
            queryset = queryset.filter(statut=statut_pesee)

        dates = {}
        for parametre, lookup in (
            ("date_min", "date__gte"),
            ("date_max", "date__lte"),
        ):
            valeur = params.get(parametre)
            date = parse_date(valeur) if valeur else None
            if valeur and date is None:
                raise ValidationError({
                    parametre: "Format invalide. Utilisez YYYY-MM-DD."
                })
            if date:
                dates[parametre] = date
                queryset = queryset.filter(**{lookup: date})
        if (
            "date_min" in dates
            and "date_max" in dates
            and dates["date_max"] < dates["date_min"]
        ):
            raise ValidationError({
                "date_max": "La date de fin doit être égale ou postérieure au début."
            })
        return queryset.order_by("-date", "-id")

    def _relire(self, pesee_id, statut_http=status.HTTP_200_OK):
        return Response(
            PeseeSerializer(self.queryset.get(pk=pesee_id)).data,
            status=statut_http,
        )

    def create(self, request, *args, **kwargs):
        serializer = PeseeEcritureSerializer(data=request.data, creation=True)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        try:
            pesee = creer_pesee(
                lot_id=donnees["id_lot_grossissement"],
                date_pesee=donnees["date"],
                nombre=donnees["nombre_poissons_peses"],
                poids_total=donnees["poids_total_g"],
                pourcentage=donnees.get("pourcentage_echantillon"),
                observation=donnees.get("observation", ""),
                confirmer=donnees.get("confirmer_echantillon_non_conforme", False),
                utilisateur=request.user,
            )
        except EchantillonNonConforme as exc:
            return _reponse_echantillon_non_conforme(exc)
        return self._relire(pesee.pk, status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="apercu")
    def apercu(self, request):
        serializer = PeseeEcritureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contexte = apercu_pesee(serializer.validated_data)
        return Response(ApercuPeseeSerializer(contexte).data)

    def partial_update(self, request, *args, **kwargs):
        pesee = self.get_object()
        serializer = PeseeEcritureSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        donnees = serializer.validated_data
        try:
            modifier_pesee(
                pesee.pk,
                donnees,
                confirmer=donnees.get("confirmer_echantillon_non_conforme", False),
            )
        except EchantillonNonConforme as exc:
            return _reponse_echantillon_non_conforme(exc)
        return self._relire(pesee.pk)

    @action(detail=True, methods=["post"], url_path="annuler")
    def annuler(self, request, pk=None):
        pesee = self.get_object()
        serializer = AnnulationPeseeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        annuler_pesee(
            pesee.pk,
            motif=serializer.validated_data["motif"],
            utilisateur=request.user,
        )
        return self._relire(pesee.pk)
