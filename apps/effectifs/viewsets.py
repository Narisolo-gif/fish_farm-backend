from django.utils import timezone
from django.utils.dateparse import parse_date

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from apps.bassins.models import Bassin, Hapa
from .calculs import effectifs_par_hapa
from .models import (
    CategoriePerte,
    CauseMortalite,
    EvenementMortalitePerte,
    MouvementEffectif,
    SortieStock,
)
from .serializers import (
    AnnulationEvenementSerializer,
    AnnulationMouvementSerializer,
    AnnulationSortieStockSerializer,
    CategoriePerteSerializer,
    CauseMortaliteSerializer,
    EvenementMortalitePerteSerializer,
    MouvementEffectifSerializer,
    SortieStockSerializer,
)
from .services import (
    annuler_sortie_stock,
    annuler_evenement_mortalite_perte,
    annuler_mouvement,
    solde_bassin_espece_date,
    valider_sortie_stock,
)


class IsRoleEffectifAutorise(BasePermission):
    ROLES_AUTORISES = {"Administrateur", "Gestionnaire", "Employé"}

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and getattr(user, "statut", None) == "ACTIF"
            and getattr(user, "role", None)
            and user.role.libelle in self.ROLES_AUTORISES
        )


class IsAdministrateurOuGestionnaireEffectif(BasePermission):
    ROLES_AUTORISES = {"Administrateur", "Gestionnaire"}

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and getattr(user, "statut", None) == "ACTIF"
            and getattr(user, "role", None)
            and user.role.libelle in self.ROLES_AUTORISES
        )


class MouvementEffectifViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MouvementEffectif.objects.select_related(
        "bassin",
        "lot_origine",
        "hapa",
        "lot_grossissement",
        "saisi_par",
        "annule_mouvement",
    ).all()
    serializer_class = MouvementEffectifSerializer
    # POST est conservé uniquement pour l'action explicite d'annulation, jamais pour créer une ligne.
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action in ["list", "retrieve", "solde", "par_hapa"]:
            return [IsAuthenticated(), IsRoleEffectifAutorise()]
        return [IsAuthenticated(), IsAdministrateurOuGestionnaireEffectif()]

    def get_queryset(self):
        queryset = super().get_queryset()

        bassin_id = self.request.query_params.get("bassin")
        espece = self.request.query_params.get("espece")
        date_evenement = self.request.query_params.get("date_evenement")

        if bassin_id:
            queryset = queryset.filter(bassin_id=bassin_id)
        if espece:
            queryset = queryset.filter(espece__iexact=espece)
        if date_evenement:
            queryset = queryset.filter(date_evenement=date_evenement)

        return queryset

    @action(
        detail=False,
        methods=["get"],
        url_path="solde",
    )
    def solde(self, request):
        bassin_id = request.query_params.get("bassin")
        espece = request.query_params.get("espece")
        date_value = request.query_params.get("date")

        if not bassin_id:
            raise ValidationError({"bassin": "Ce paramètre est obligatoire."})
        if not espece:
            raise ValidationError({"espece": "Ce paramètre est obligatoire."})
        if espece not in dict(MouvementEffectif._meta.get_field("espece").choices):
            raise ValidationError({"espece": "Espèce inconnue."})

        if date_value:
            date_reference = parse_date(date_value)
            if date_reference is None:
                raise ValidationError({
                    "date": "Format invalide. Utilisez YYYY-MM-DD."
                })
        else:
            date_reference = timezone.localdate()

        try:
            bassin = Bassin.objects.get(pk=bassin_id)
        except (Bassin.DoesNotExist, ValueError, TypeError) as exc:
            raise NotFound("Bassin introuvable.") from exc

        # La date par défaut est la date locale de la ferme, pas l'heure de saisie.
        return Response({
            "bassin": bassin.pk,
            "reference_bassin": bassin.reference,
            "espece": espece,
            "date": date_reference,
            "effectif_vivant": solde_bassin_espece_date(
                bassin.pk,
                espece,
                date_reference,
            ),
        })

    @action(detail=False, methods=["get"], url_path="par-hapa")
    def par_hapa(self, request):
        bassin_id = request.query_params.get("bassin")
        if not bassin_id:
            raise ValidationError({"bassin": "Ce paramètre est obligatoire."})
        date_value = request.query_params.get("date")
        date_reference = parse_date(date_value) if date_value else timezone.localdate()
        if date_reference is None:
            raise ValidationError({"date": "Format invalide. Utilisez YYYY-MM-DD."})
        try:
            bassin = Bassin.objects.get(pk=bassin_id)
        except (Bassin.DoesNotExist, ValueError, TypeError) as exc:
            raise NotFound("Bassin introuvable.") from exc

        balances = effectifs_par_hapa(bassin.pk, a_la_date=date_reference)
        hapas = Hapa.objects.filter(bassin=bassin).order_by("nom", "id")
        rows = [
            {
                "id_hapa": hapa.pk,
                "nom_hapa": hapa.nom,
                "espece": espece,
                "effectif": effectif,
            }
            for (hapa_id, espece), effectif in balances.items()
            for hapa in hapas
            if hapa.pk == hapa_id
        ]
        return Response({
            "bassin": bassin.pk,
            "date": date_reference,
            "effectifs": rows,
        })

    @action(
        detail=True,
        methods=["post"],
        url_path="annuler",
    )
    def annuler(self, request, pk=None):
        mouvement = self.get_object()
        serializer = AnnulationMouvementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        annulation = annuler_mouvement(
            mouvement.pk,
            motif_annulation=serializer.validated_data["motif_annulation"],
            saisi_par=request.user,
            date_annulation=serializer.validated_data.get("date_annulation"),
        )

        return Response(
            MouvementEffectifSerializer(annulation).data,
            status=status.HTTP_201_CREATED,
        )


class CauseMortaliteViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = CauseMortalite.objects.all()
    serializer_class = CauseMortaliteSerializer
    permission_classes = [IsAuthenticated, IsRoleEffectifAutorise]

    def get_queryset(self):
        queryset = super().get_queryset()
        actif = self.request.query_params.get("actif")
        if actif is not None:
            if actif.lower() not in {"true", "false"}:
                raise ValidationError({"actif": "Utilisez true ou false."})
            queryset = queryset.filter(actif=actif.lower() == "true")
        return queryset


class CategoriePerteViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = CategoriePerte.objects.all()
    serializer_class = CategoriePerteSerializer
    permission_classes = [IsAuthenticated, IsRoleEffectifAutorise]

    def get_queryset(self):
        queryset = super().get_queryset()
        actif = self.request.query_params.get("actif")
        if actif is not None:
            if actif.lower() not in {"true", "false"}:
                raise ValidationError({"actif": "Utilisez true ou false."})
            queryset = queryset.filter(actif=actif.lower() == "true")
        return queryset


class EvenementMortalitePerteViewSet(viewsets.ModelViewSet):
    queryset = EvenementMortalitePerte.objects.select_related(
        "bassin",
        "cause_mortalite",
        "categorie_perte",
        "mouvement",
        "saisi_par",
    ).all()
    serializer_class = EvenementMortalitePerteSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [IsAuthenticated(), IsRoleEffectifAutorise()]
        return [IsAuthenticated(), IsAdministrateurOuGestionnaireEffectif()]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        if params.get("bassin"):
            if not params["bassin"].isdigit():
                raise ValidationError({"bassin": "Identifiant de bassin invalide."})
            queryset = queryset.filter(bassin_id=params["bassin"])
        if params.get("espece"):
            queryset = queryset.filter(espece__iexact=params["espece"])
        if params.get("type_evenement"):
            queryset = queryset.filter(type_evenement=params["type_evenement"])

        for query_param, field in (
            ("date_debut", "date_evenement__gte"),
            ("date_fin", "date_evenement__lte"),
        ):
            value = params.get(query_param)
            if value:
                parsed = parse_date(value)
                if parsed is None:
                    raise ValidationError({
                        query_param: "Format invalide. Utilisez YYYY-MM-DD."
                    })
                queryset = queryset.filter(**{field: parsed})

        if params.get("cause"):
            if not params["cause"].isdigit():
                raise ValidationError({"cause": "Identifiant de cause invalide."})
            queryset = queryset.filter(cause_mortalite_id=params["cause"])
        if params.get("categorie"):
            if not params["categorie"].isdigit():
                raise ValidationError({
                    "categorie": "Identifiant de catégorie invalide."
                })
            queryset = queryset.filter(categorie_perte_id=params["categorie"])

        date_debut = params.get("date_debut")
        date_fin = params.get("date_fin")
        if date_debut and date_fin and date_debut > date_fin:
            raise ValidationError({
                "date_fin": "La date de fin doit être égale ou postérieure à la date de début."
            })
        return queryset

    @action(
        detail=True,
        methods=["post"],
        url_path="annuler",
    )
    def annuler(self, request, pk=None):
        evenement = self.get_object()
        serializer = AnnulationEvenementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        evenement, mouvement_annulation = annuler_evenement_mortalite_perte(
            evenement.pk,
            motif_annulation=serializer.validated_data["motif_annulation"],
            saisi_par=request.user,
        )
        return Response(
            {
                "evenement": EvenementMortalitePerteSerializer(evenement).data,
                "mouvement_annulation": MouvementEffectifSerializer(
                    mouvement_annulation
                ).data,
            },
            status=status.HTTP_201_CREATED,
        )


class SortieStockViewSet(viewsets.ModelViewSet):
    queryset = SortieStock.objects.select_related(
        "bassin_source",
        "bassin_destination",
        "utilisateur",
        "validee_par",
        "annulee_par",
    ).all()
    serializer_class = SortieStockSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        if self.action in {"list", "retrieve", "create"}:
            return [IsAuthenticated(), IsRoleEffectifAutorise()]
        return [IsAuthenticated(), IsAdministrateurOuGestionnaireEffectif()]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        type_sortie = params.get("type_sortie")
        if type_sortie:
            if type_sortie not in SortieStock.TypeSortie.values:
                raise ValidationError({"type_sortie": "Type de sortie inconnu."})
            queryset = queryset.filter(type_sortie=type_sortie)

        statut = params.get("statut")
        if statut:
            if statut not in SortieStock.Statut.values:
                raise ValidationError({"statut": "Statut inconnu."})
            queryset = queryset.filter(statut=statut)

        bassin_source = params.get("bassin_source")
        if bassin_source:
            if not bassin_source.isdigit():
                raise ValidationError({
                    "bassin_source": "Identifiant de bassin invalide."
                })
            queryset = queryset.filter(bassin_source_id=bassin_source)

        espece = params.get("espece")
        if espece:
            if espece not in dict(MouvementEffectif._meta.get_field("espece").choices):
                raise ValidationError({"espece": "Espèce inconnue."})
            queryset = queryset.filter(espece=espece)

        date_min_value = params.get("date_min")
        date_max_value = params.get("date_max")
        date_min = parse_date(date_min_value) if date_min_value else None
        date_max = parse_date(date_max_value) if date_max_value else None
        if date_min_value and date_min is None:
            raise ValidationError({
                "date_min": "Format invalide. Utilisez YYYY-MM-DD."
            })
        if date_max_value and date_max is None:
            raise ValidationError({
                "date_max": "Format invalide. Utilisez YYYY-MM-DD."
            })
        if date_min and date_max and date_max < date_min:
            raise ValidationError({
                "date_max": "La date de fin doit être égale ou postérieure au début."
            })
        if date_min:
            queryset = queryset.filter(date_sortie__date__gte=date_min)
        if date_max:
            queryset = queryset.filter(date_sortie__date__lte=date_max)
        return queryset

    @action(detail=True, methods=["post"], url_path="valider")
    def valider(self, request, pk=None):
        sortie = valider_sortie_stock(
            self.get_object().pk,
            utilisateur=request.user,
        )
        return Response(self.get_serializer(sortie).data)

    @action(detail=True, methods=["post"], url_path="annuler")
    def annuler(self, request, pk=None):
        serializer = AnnulationSortieStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sortie = annuler_sortie_stock(
            self.get_object().pk,
            motif=serializer.validated_data["motif"],
            utilisateur=request.user,
        )
        return Response(self.get_serializer(sortie).data)
