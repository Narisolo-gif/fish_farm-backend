from django.utils import timezone
from django.utils.dateparse import parse_date

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from apps.bassins.models import Bassin
from .models import (
    CategoriePerte,
    CauseMortalite,
    EvenementMortalitePerte,
    MouvementEffectif,
)
from .serializers import (
    AnnulationEvenementSerializer,
    AnnulationMouvementSerializer,
    CategoriePerteSerializer,
    CauseMortaliteSerializer,
    EvenementMortalitePerteSerializer,
    MouvementEffectifSerializer,
)
from .services import (
    annuler_evenement_mortalite_perte,
    annuler_mouvement,
    solde_bassin_espece_date,
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
        if self.action in ["list", "retrieve", "solde"]:
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
