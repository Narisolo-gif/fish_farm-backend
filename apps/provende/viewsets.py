from django.utils.dateparse import parse_date

from django.db.models.deletion import ProtectedError

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from apps.bassins.models import Bassin
from apps.core.permissions import (
    IsAdministrateurOuGestionnaire,
    IsTousRolesActifs,
)

from .models import ConsommationProvende, Provende, TypeProvende
from .serializers import (
    AnnulationConsommationSerializer,
    ConsommationProvendeSerializer,
    ProvendeSerializer,
    TypeProvendeSerializer,
)
from .services import (
    annuler_distribution,
    jours_manquants,
    total_journalier,
)


class ProvendeViewSet(viewsets.ModelViewSet):
    """API CRUD des provendes."""

    queryset = Provende.objects.select_related("type_provende").all()
    serializer_class = ProvendeSerializer

    def get_permissions(self):
        # Tous les rôles actifs peuvent consulter.
        if self.action in ["list", "retrieve"]:
            return [IsTousRolesActifs()]

        # Seuls Admin et Gestionnaire peuvent modifier les données.
        return [IsAdministrateurOuGestionnaire()]

    def perform_destroy(self, instance):
        """Transforme une suppression protégée en erreur API contrôlée."""

        try:
            instance.delete()
        except ProtectedError as exc:
            raise ValidationError({
                "detail": (
                    "Cette provende est utilisée par un traitement "
                    "et ne peut pas être supprimée."
                )
            }) from exc


class TypeProvendeViewSet(viewsets.ModelViewSet):
    """API CRUD des types de provende."""

    queryset = TypeProvende.objects.all()
    serializer_class = TypeProvendeSerializer

    def get_permissions(self):
        # Tous les rôles actifs peuvent consulter les types.
        if self.action in ["list", "retrieve"]:
            return [IsTousRolesActifs()]

        return [IsAdministrateurOuGestionnaire()]

    def perform_destroy(self, instance):
        """Empêche la suppression d'un type encore référencé."""

        try:
            instance.delete()
        except ProtectedError as exc:
            raise ValidationError({
                "detail": (
                    "Ce type de provende est associé à une ou plusieurs "
                    "provendes et ne peut pas être supprimé."
                )
            }) from exc


class IsGestionnaireOuAdministrateurStatutActif(BasePermission):
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


class ConsommationProvendeViewSet(viewsets.ModelViewSet):
    queryset = ConsommationProvende.objects.select_related(
        "bassin",
        "provende",
        "provende__type_provende",
        "utilisateur",
        "annulee_par",
    ).all()
    serializer_class = ConsommationProvendeSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action in {
            "list",
            "retrieve",
            "totaux_journaliers",
            "jours_manquants_action",
        }:
            return [IsTousRolesActifs()]
        if self.action == "annuler":
            return [IsGestionnaireOuAdministrateurStatutActif()]
        return [IsAuthenticated(), IsTousRolesActifs()]

    @staticmethod
    def _dates_periode(params, *, max_days=None):
        date_min_value = params.get("date_min")
        date_max_value = params.get("date_max")
        if not date_min_value:
            raise ValidationError({"date_min": "Ce paramètre est obligatoire."})
        if not date_max_value:
            raise ValidationError({"date_max": "Ce paramètre est obligatoire."})

        date_min = parse_date(date_min_value)
        date_max = parse_date(date_max_value)
        if date_min is None:
            raise ValidationError({
                "date_min": "Format invalide. Utilisez YYYY-MM-DD."
            })
        if date_max is None:
            raise ValidationError({
                "date_max": "Format invalide. Utilisez YYYY-MM-DD."
            })
        if date_max < date_min:
            raise ValidationError({
                "date_max": "La date de fin doit être égale ou postérieure à la date de début."
            })
        if max_days is not None and (date_max - date_min).days + 1 > max_days:
            raise ValidationError({
                "date_max": f"La période ne peut pas dépasser {max_days} jours."
            })
        return date_min, date_max

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        bassin_id = params.get("bassin")
        if bassin_id:
            if not bassin_id.isdigit():
                raise ValidationError({"bassin": "Identifiant de bassin invalide."})
            queryset = queryset.filter(bassin_id=bassin_id)

        provende_id = params.get("provende")
        if provende_id:
            if not provende_id.isdigit():
                raise ValidationError({"provende": "Identifiant de provende invalide."})
            queryset = queryset.filter(provende_id=provende_id)

        type_value = params.get("type_provende")
        if type_value:
            type_labels = {
                code.lower(): code
                for code, _ in TypeProvende.Type.choices
            }
            type_labels.update({
                label.lower(): code
                for code, label in TypeProvende.Type.choices
            })
            type_code = type_labels.get(type_value.lower())
            if type_code is None:
                raise ValidationError({"type_provende": "Type de provende inconnu."})
            queryset = queryset.filter(provende__type_provende__type=type_code)

        statut = params.get("statut")
        if statut:
            if statut not in ConsommationProvende.Statut.values:
                raise ValidationError({"statut": "Statut de consommation inconnu."})
            queryset = queryset.filter(statut=statut)

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
                "date_max": "La date de fin doit être égale ou postérieure à la date de début."
            })
        if date_min:
            queryset = queryset.filter(
                date_distribution__date__gte=date_min,
            )
        if date_max:
            queryset = queryset.filter(date_distribution__date__lte=date_max)
        return queryset

    @action(detail=False, methods=["get"], url_path="totaux-journaliers")
    def totaux_journaliers(self, request):
        bassin_id = request.query_params.get("bassin")
        if not bassin_id or not bassin_id.isdigit():
            raise ValidationError({"bassin": "Un identifiant de bassin valide est obligatoire."})
        if not Bassin.objects.filter(pk=bassin_id).exists():
            raise ValidationError({"bassin": "Bassin introuvable."})
        date_min, date_max = self._dates_periode(request.query_params)
        resultats = total_journalier(bassin_id, date_min, date_max)
        return Response(
            [
                {
                    **jour,
                    "date": jour["date"].isoformat(),
                }
                for jour in resultats
            ]
        )

    @action(detail=False, methods=["get"], url_path="jours-manquants")
    def jours_manquants_action(self, request):
        bassin_id = request.query_params.get("bassin")
        if not bassin_id or not bassin_id.isdigit():
            raise ValidationError({"bassin": "Un identifiant de bassin valide est obligatoire."})
        if not Bassin.objects.filter(pk=bassin_id).exists():
            raise ValidationError({"bassin": "Bassin introuvable."})
        date_min, date_max = self._dates_periode(
            request.query_params,
            max_days=366,
        )
        resultats = jours_manquants(bassin_id, date_min, date_max)
        return Response([jour.isoformat() for jour in resultats])

    @action(detail=True, methods=["post"], url_path="annuler")
    def annuler(self, request, pk=None):
        serializer = AnnulationConsommationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        distribution = annuler_distribution(
            self.get_object().pk,
            motif=serializer.validated_data["motif"],
            utilisateur=request.user,
        )
        return Response(
            self.get_serializer(distribution).data,
            status=status.HTTP_200_OK,
        )