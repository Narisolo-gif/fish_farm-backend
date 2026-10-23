from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, ExpressionWrapper, F, IntegerField, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from rest_framework.exceptions import ValidationError

from apps.bassins.models import Bassin
from apps.effectifs.calculs import effectifs_du_bassin
from apps.effectifs.models import MouvementEffectif

from .models import ConsommationProvende, Provende, TypeProvende


@transaction.atomic
def creer_type_provende(validated_data):
    """Crée un type de provende dans une transaction."""

    return TypeProvende.objects.create(**validated_data)


@transaction.atomic
def creer_provende(validated_data):
    """Crée une provende dans une transaction."""

    return Provende.objects.create(**validated_data)


@transaction.atomic
def enregistrer_distribution(validated_data, utilisateur):
    bassin = validated_data["bassin"]
    provende = validated_data["provende"]

    # Le verrou du bassin sérialise les distributions et les autres mouvements d'effectif.
    bassin = Bassin.objects.select_for_update().get(pk=bassin.pk)
    if bassin.statut != Bassin.Statut.ACTIF:
        raise ValidationError({
            "id_bassin": "Le bassin sélectionné est inactif."
        })

    date_distribution = validated_data.get("date_distribution") or timezone.now()
    date_distribution = date_distribution.replace(second=0, microsecond=0)
    if date_distribution > timezone.now():
        raise ValidationError({
            "date_distribution": "La date et l’heure ne peuvent pas être dans le futur."
        })

    quantite_kg = validated_data["quantite_kg"]
    if quantite_kg < 0:
        raise ValidationError({
            "quantite_kg": "La quantité ne peut pas être négative."
        })

    date_locale = timezone.localtime(date_distribution).date()
    if date_locale < provende.date_creation:
        raise ValidationError({
            "id_provende": (
                "La distribution ne peut pas précéder la date de création "
                "de la provende."
            )
        })

    effectifs = effectifs_du_bassin(bassin.pk, a_la_date=date_locale)
    if not any(effectif > 0 for effectif in effectifs.values()):
        raise ValidationError({
            "id_bassin": (
                "Aucun poisson vivant n’est enregistré dans ce bassin à "
                "la date de distribution."
            )
        })

    if ConsommationProvende.objects.filter(
        bassin=bassin,
        provende=provende,
        date_distribution=date_distribution,
        statut=ConsommationProvende.Statut.VALIDE,
    ).exists():
        raise ValidationError({
            "date_distribution": (
                "Une distribution valide existe déjà pour ce bassin, "
                "cette provende et cette minute."
            )
        })

    try:
        # La contrainte unique reste la protection finale contre une course concurrente.
        with transaction.atomic():
            return ConsommationProvende.objects.create(
                bassin=bassin,
                provende=provende,
                date_distribution=date_distribution,
                quantite_kg=quantite_kg,
                observation=validated_data.get("observation", ""),
                utilisateur=utilisateur,
            )
    except IntegrityError as exc:
        cause = exc.__cause__
        constraint_name = getattr(
            getattr(cause, "diag", None),
            "constraint_name",
            None,
        )
        sqlite_unique_error = (
            "UNIQUE constraint failed: "
            "provende_consommationprovende.bassin_id, "
            "provende_consommationprovende.provende_id, "
            "provende_consommationprovende.date_distribution"
        )
        if (
            constraint_name == "consommation_provende_unique_valide"
            or sqlite_unique_error in str(cause)
        ):
            raise ValidationError({
                "date_distribution": (
                    "Une distribution valide existe déjà pour ce bassin, "
                    "cette provende et cette minute."
                )
            }) from exc
        raise


@transaction.atomic
def annuler_distribution(distribution_id, motif, utilisateur):
    distribution = (
        ConsommationProvende.objects
        .select_for_update()
        .get(pk=distribution_id)
    )
    if distribution.statut != ConsommationProvende.Statut.VALIDE:
        raise ValidationError({
            "detail": "Seule une distribution valide peut être annulée."
        })
    if not motif or not motif.strip():
        raise ValidationError({
            "motif": "Le motif d’annulation est obligatoire."
        })

    distribution.statut = ConsommationProvende.Statut.ANNULEE
    distribution.motif_annulation = motif.strip()
    distribution.annulee_par = utilisateur
    distribution.annulee_le = timezone.now()
    distribution.save(update_fields=[
        "statut",
        "motif_annulation",
        "annulee_par",
        "annulee_le",
    ])
    return distribution


def total_journalier(bassin_id, date_min: date, date_max: date):
    jour_local = TruncDate(
        "date_distribution",
        tzinfo=timezone.get_current_timezone(),
    )
    lignes = (
        ConsommationProvende.objects
        .filter(
            bassin_id=bassin_id,
            statut=ConsommationProvende.Statut.VALIDE,
            date_distribution__date__gte=date_min,
            date_distribution__date__lte=date_max,
        )
        .annotate(jour=jour_local)
        .values("jour", "provende__type_provende__type")
        .annotate(
            total_kg=Sum("quantite_kg"),
            nombre_distributions=Count("id"),
        )
        .order_by("jour", "provende__type_provende__type")
    )

    labels = dict(TypeProvende.Type.choices)
    jours = {}
    for ligne in lignes:
        jour = ligne["jour"]
        total = jours.setdefault(
            jour,
            {
                "date": jour,
                "total_kg": Decimal("0.00"),
                "nombre_distributions": 0,
                "detail_par_type": [],
            },
        )
        total["total_kg"] += ligne["total_kg"] or Decimal("0.00")
        total["nombre_distributions"] += ligne["nombre_distributions"]
        type_code = ligne["provende__type_provende__type"]
        total["detail_par_type"].append({
            "type_provende": labels.get(type_code, type_code),
            "total_kg": ligne["total_kg"] or Decimal("0.00"),
            "nombre_distributions": ligne["nombre_distributions"],
        })
    return list(jours.values())


def jours_manquants(bassin_id, date_min: date, date_max: date):
    nombre_jours = (date_max - date_min).days + 1
    if nombre_jours <= 0:
        raise ValidationError({
            "date_max": "La date de fin doit être égale ou postérieure à la date de début."
        })
    if nombre_jours > 366:
        raise ValidationError({
            "date_max": "La période ne peut pas dépasser 366 jours."
        })

    effectifs = effectifs_du_bassin(
        bassin_id,
        a_la_date=date_min - timedelta(days=1),
    )
    changements = {}
    mouvements = (
        MouvementEffectif.objects
        .filter(
            bassin_id=bassin_id,
            date_evenement__gte=date_min,
            date_evenement__lte=date_max,
        )
        .annotate(
            contribution=ExpressionWrapper(
                F("sens") * F("quantite"),
                output_field=IntegerField(),
            )
        )
        .values("date_evenement", "espece")
        .annotate(variation=Sum("contribution"))
        .order_by("date_evenement", "espece")
    )
    for mouvement in mouvements:
        variations_du_jour = changements.setdefault(
            mouvement["date_evenement"],
            {},
        )
        variations_du_jour[mouvement["espece"]] = mouvement["variation"] or 0

    jour_local = TruncDate(
        "date_distribution",
        tzinfo=timezone.get_current_timezone(),
    )
    jours_avec_distribution = set(
        ConsommationProvende.objects
        .filter(
            bassin_id=bassin_id,
            statut=ConsommationProvende.Statut.VALIDE,
            date_distribution__date__gte=date_min,
            date_distribution__date__lte=date_max,
        )
        .annotate(jour=jour_local)
        .values_list("jour", flat=True)
        .distinct()
    )

    resultats = []
    jour = date_min
    while jour <= date_max:
        for espece, variation in changements.get(jour, {}).items():
            effectifs[espece] = effectifs.get(espece, 0) + variation
        if sum(effectifs.values()) > 0 and jour not in jours_avec_distribution:
            resultats.append(jour)
        jour += timedelta(days=1)
    return resultats