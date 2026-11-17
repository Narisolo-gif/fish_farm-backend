from django.utils import timezone
from django.utils.dateparse import parse_date

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from apps.bassins.models import Bassin, Hapa
from apps.core.permissions import PermissionMatrice

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
    annuler_sortie,
    annuler_evenement,
    annuler_mouvement,
    solde_bassin_espece_date,
    totaux_evenements,
)


class MouvementEffectifViewSet(viewsets.ReadOnlyModelViewSet):

    ressource = "mouvements"
    permission_classes = [PermissionMatrice]
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


class CauseMortaliteViewSet(viewsets.ModelViewSet):

    ressource = "causes_mortalite"
    permission_classes = [PermissionMatrice]
    queryset = CauseMortalite.objects.all()
    serializer_class = CauseMortaliteSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        actif = self.request.query_params.get("actif")
        if actif is not None:
            if actif.lower() not in {"true", "false"}:
                raise ValidationError({"actif": "Utilisez true ou false."})
            queryset = queryset.filter(actif=actif.lower() == "true")
        return queryset


class CategoriePerteViewSet(viewsets.ModelViewSet):

    ressource = "categories_perte"
    permission_classes = [PermissionMatrice]
    queryset = CategoriePerte.objects.all()
    serializer_class = CategoriePerteSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        actif = self.request.query_params.get("actif")
        if actif is not None:
            if actif.lower() not in {"true", "false"}:
                raise ValidationError({"actif": "Utilisez true ou false."})
            queryset = queryset.filter(actif=actif.lower() == "true")
        return queryset


class EvenementMortalitePerteViewSet(viewsets.ModelViewSet):

    ressource = "evenements_mortalite_perte"
    permission_classes = [PermissionMatrice]
    queryset = EvenementMortalitePerte.objects.select_related(
        "bassin",
        "hapa",
        "cause_mortalite",
        "categorie_perte",
        "mouvement",
        "utilisateur",
        "annulee_par",
    ).all()
    serializer_class = EvenementMortalitePerteSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        for parametre, champ in (
            ("bassin", "bassin_id"),
            ("hapa", "hapa_id"),
            ("cause", "cause_mortalite_id"),
            ("categorie", "categorie_perte_id"),
        ):
            value = params.get(parametre)
            if value:
                if not value.isdigit():
                    raise ValidationError({
                        parametre: f"Identifiant {parametre} invalide."
                    })
                queryset = queryset.filter(**{champ: value})
        if params.get("espece"):
            if params["espece"] not in dict(MouvementEffectif._meta.get_field("espece").choices):
                raise ValidationError({"espece": "Espèce inconnue."})
            queryset = queryset.filter(espece=params["espece"])
        if params.get("type_evenement"):
            if params["type_evenement"] not in EvenementMortalitePerte.TypeEvenement.values:
                raise ValidationError({"type_evenement": "Type d’événement invalide."})
            queryset = queryset.filter(type_evenement=params["type_evenement"])
        if params.get("statut"):
            if params["statut"] not in EvenementMortalitePerte.Statut.values:
                raise ValidationError({"statut": "Statut invalide."})
            queryset = queryset.filter(statut=params["statut"])

        dates = {}
        for parametre in ("date_min", "date_max"):
            value = params.get(parametre)
            if value:
                parsed = parse_date(value)
                if parsed is None:
                    raise ValidationError({
                        parametre: "Format invalide. Utilisez YYYY-MM-DD."
                    })
                dates[parametre] = parsed
                lookup = (
                    "date_evenement__date__gte"
                    if parametre == "date_min"
                    else "date_evenement__date__lte"
                )
                queryset = queryset.filter(**{lookup: parsed})

        if (
            dates.get("date_min")
            and dates.get("date_max")
            and dates["date_min"] > dates["date_max"]
        ):
            raise ValidationError({
                "date_max": "La date maximale doit être égale ou postérieure à la date minimale."
            })
        return queryset.order_by("-date_evenement", "-id")

    @action(
        detail=True,
        methods=["post"],
        url_path="annuler",
    )
    def annuler(self, request, pk=None):
        evenement = self.get_object()
        serializer = AnnulationEvenementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        evenement = annuler_evenement(
            evenement.pk,
            motif=serializer.validated_data["motif"],
            utilisateur=request.user,
        )
        return Response(
            EvenementMortalitePerteSerializer(evenement).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get"], url_path="totaux")
    def totaux(self, request):
        for parametre in ("date_min", "date_max"):
            value = request.query_params.get(parametre)
            if value and parse_date(value) is None:
                raise ValidationError({
                    parametre: "Format invalide. Utilisez YYYY-MM-DD."
                })
        return Response(totaux_evenements(request.query_params))


class SortieStockViewSet(viewsets.ReadOnlyModelViewSet):
    """Consultation et annulation des sorties de stock.

    La sortie vers le grossissement se crée et se valide en un seul appel,
    avec la création du lot : POST /api/v1/grossissement/lots-grossissement/.
    Il n'y a plus de brouillon ni de validation en deux temps.
    """

    ressource = "sorties_stock"
    permission_classes = [PermissionMatrice]

    queryset = SortieStock.objects.select_related(
        "lot",
        "bassin_destination",
        "utilisateur",
        "validee_par",
        "annulee_par",
    ).prefetch_related("lignes__hapa__bassin").all()
    serializer_class = SortieStockSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        statut = params.get("statut")
        if statut:
            if statut not in SortieStock.Statut.values:
                raise ValidationError({"statut": "Statut inconnu."})
            queryset = queryset.filter(statut=statut)

        for parametre, champ in (
            ("lot", "lot_id"),
            ("bassin_destination", "bassin_destination_id"),
        ):
            valeur = params.get(parametre)
            if valeur:
                if not valeur.isdigit():
                    raise ValidationError({
                        parametre: "Identifiant invalide."
                    })
                queryset = queryset.filter(**{champ: valeur})

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
        return queryset.order_by("-date_sortie", "-id")

    @action(detail=True, methods=["post"], url_path="annuler")
    def annuler(self, request, pk=None):
        serializer = AnnulationSortieStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sortie = annuler_sortie(
            self.get_object().pk,
            motif=serializer.validated_data["motif"],
            utilisateur=request.user,
        )
        return Response(self.get_serializer(sortie).data)
