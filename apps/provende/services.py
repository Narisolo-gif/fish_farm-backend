from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, ExpressionWrapper, F, IntegerField, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from rest_framework.exceptions import ValidationError

from apps.bassins.models import Bassin, Hapa
from apps.effectifs.calculs import effectifs_du_bassin, effectifs_par_hapa
from apps.effectifs.services import _verifier_hapa
from apps.effectifs.models import MouvementEffectif

from .models import ConsommationProvende, Provende, TypeProvende

MAX_DISTRIBUTIONS_PAR_JOUR = 3


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
    hapa = validated_data.get("hapa")

    bassin = Bassin.objects.select_for_update().get(pk=bassin.pk)
    if bassin.statut != Bassin.Statut.ACTIF:
        raise ValidationError({
            "id_bassin": "Le bassin sélectionné est inactif."
        })
    hapa_obligatoire = bassin.categorie in {
        Bassin.Categorie.TRAITEMENT,
        Bassin.Categorie.STOCKS,
    }
    if (
        hapa is None
        and hapa_obligatoire
        and not Hapa.objects.filter(
            bassin=bassin,
            actif=True,
            statut=Hapa.Statut.ACTIF,
        ).exists()
    ):
        raise ValidationError({
            "id_hapa": "Aucun hapa actif dans ce bassin : créez d'abord des hapas."
        })
    _verifier_hapa(bassin, hapa, champ="id_hapa")
    if hapa is not None:
        hapa = Hapa.objects.select_for_update().get(pk=hapa.pk)

    date_distribution = validated_data.get("date_distribution") or timezone.now()
    if timezone.is_naive(date_distribution):
        date_distribution = timezone.make_aware(
            date_distribution,
            timezone.get_current_timezone(),
        )
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

    if hapa is not None:
        effectifs = effectifs_par_hapa(bassin.pk, a_la_date=date_locale)
        effectif_cible = sum(
            effectif
            for (hapa_id, _), effectif in effectifs.items()
            if hapa_id == hapa.pk
        )
        champ_cible = "id_hapa"
    else:
        effectifs = effectifs_du_bassin(bassin.pk, a_la_date=date_locale)
        effectif_cible = sum(effectifs.values())
        champ_cible = "id_bassin"
    if effectif_cible <= 0:
        raise ValidationError({
            champ_cible: "Aucun poisson vivant n’est enregistré dans cette cible à cette date."
        })

    debut_jour = timezone.make_aware(
        datetime.combine(date_locale, time.min),
        timezone.get_current_timezone(),
    )
    fin_jour = timezone.make_aware(
        datetime.combine(date_locale + timedelta(days=1), time.min),
        timezone.get_current_timezone(),
    )
    distributions_jour = ConsommationProvende.objects.filter(
        bassin=bassin,
        statut=ConsommationProvende.Statut.VALIDE,
        date_distribution__gte=debut_jour,
        date_distribution__lt=fin_jour,
    )
    distributions_jour = (
        distributions_jour.filter(hapa=hapa)
        if hapa is not None
        else distributions_jour.filter(hapa__isnull=True)
    )
    if distributions_jour.count() >= MAX_DISTRIBUTIONS_PAR_JOUR:
        raise ValidationError({
            "date_distribution": (
                f"Maximum de {MAX_DISTRIBUTIONS_PAR_JOUR} distributions par jour "
                "atteint pour ce hapa/bassin."
            )
        })

    doublons = distributions_jour.filter(
        provende=provende,
        date_distribution=date_distribution,
    )
    if doublons.exists():
        raise ValidationError({
            "date_distribution": (
                "Une distribution valide existe déjà pour ce bassin, "
                "ce hapa et cette provende à cette minute."
            )
        })

    try:
        with transaction.atomic():
            return ConsommationProvende.objects.create(
                bassin=bassin,
                hapa=hapa,
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
        if (
            constraint_name in {
                "consommation_provende_unique_valide_sans_hapa",
                "consommation_provende_unique_valide_avec_hapa",
            }
            or "UNIQUE constraint failed: provende_consommationprovende." in str(cause)
        ):
            raise ValidationError({
                "date_distribution": (
                    "Une distribution valide existe déjà pour ce bassin, "
                    "ce hapa, cette provende et cette minute."
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

    Bassin.objects.select_for_update().get(pk=distribution.bassin_id)
    if distribution.hapa_id is not None:
        Hapa.objects.select_for_update().get(pk=distribution.hapa_id)

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


def total_journalier(bassin_id, date_min: date, date_max: date, hapa_id=None):
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
    )
    if hapa_id is not None:
        lignes = lignes.filter(hapa_id=hapa_id)
    lignes = lignes.annotate(jour=jour_local)
    lignes = (
        lignes.values("jour", "provende__type_provende__type")
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
    for total in jours.values():
        total["distributions_restantes"] = max(
            0,
            MAX_DISTRIBUTIONS_PAR_JOUR - total["nombre_distributions"],
        )
    return list(jours.values())


def jours_manquants(bassin_id, date_min: date, date_max: date, hapa_id=None):
    nombre_jours = (date_max - date_min).days + 1
    if nombre_jours <= 0:
        raise ValidationError({
            "date_max": "La date de fin doit être égale ou postérieure à la date de début."
        })
    if nombre_jours > 366:
        raise ValidationError({
            "date_max": "La période ne peut pas dépasser 366 jours."
        })

    bassin = Bassin.objects.get(pk=bassin_id)
    hapa_obligatoire = bassin.categorie in {
        Bassin.Categorie.TRAITEMENT,
        Bassin.Categorie.STOCKS,
    }
    if hapa_id is not None:
        if not hapa_obligatoire and bassin.categorie in {
            Bassin.Categorie.REPRODUCTION,
            Bassin.Categorie.GROSSISSEMENT,
        }:
            raise ValidationError({
                "hapa": "Un hapa est interdit pour cette catégorie de bassin."
            })
        cible = Hapa.objects.filter(
            pk=hapa_id,
            bassin_id=bassin_id,
            actif=True,
            statut=Hapa.Statut.ACTIF,
        ).first()
        if cible is None:
            raise ValidationError({"hapa": "Hapa actif introuvable dans ce bassin."})
        cibles_hapas = [cible]
    elif hapa_obligatoire:
        cibles_hapas = list(
            Hapa.objects.filter(
                bassin_id=bassin_id,
                actif=True,
                statut=Hapa.Statut.ACTIF,
            ).order_by("pk")
        )
    else:
        cibles_hapas = []

    par_hapa = hapa_obligatoire or hapa_id is not None
    if par_hapa:
        cibles_ids = [hapa.pk for hapa in cibles_hapas]
        initial = effectifs_par_hapa(
            bassin_id,
            a_la_date=date_min - timedelta(days=1),
        )
        effectifs = {
            hapa.pk: {
                espece: effectif
                for (id_hapa, espece), effectif in initial.items()
                if id_hapa == hapa.pk
            }
            for hapa in cibles_hapas
        }
        changements = {}
        mouvements = (
            MouvementEffectif.objects
            .filter(
                bassin_id=bassin_id,
                hapa_id__in=cibles_ids,
                date_evenement__gte=date_min,
                date_evenement__lte=date_max,
            )
            .annotate(
                contribution=ExpressionWrapper(
                    F("sens") * F("quantite"),
                    output_field=IntegerField(),
                )
            )
            .values("date_evenement", "hapa_id", "espece")
            .annotate(variation=Sum("contribution"))
        )
        for mouvement in mouvements:
            jour_cibles = changements.setdefault(
                mouvement["date_evenement"],
                {},
            )
            jour_especes = jour_cibles.setdefault(mouvement["hapa_id"], {})
            jour_especes[mouvement["espece"]] = mouvement["variation"] or 0
    else:
        cibles_ids = []
        effectifs = {
            None: effectifs_du_bassin(
                bassin_id,
                a_la_date=date_min - timedelta(days=1),
            )
        }
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
        )
        for mouvement in mouvements:
            changements.setdefault(mouvement["date_evenement"], {})[
                mouvement["espece"]
            ] = mouvement["variation"] or 0

    jour_local = TruncDate(
        "date_distribution",
        tzinfo=timezone.get_current_timezone(),
    )
    distributions = (
        ConsommationProvende.objects
        .filter(
            bassin_id=bassin_id,
            statut=ConsommationProvende.Statut.VALIDE,
            date_distribution__date__gte=date_min,
            date_distribution__date__lte=date_max,
        )
        .annotate(jour=jour_local)
    )
    if par_hapa:
        distributions = distributions.filter(hapa_id__in=cibles_ids)
        jours_avec_distribution = set(
            distributions.values_list("hapa_id", "jour").distinct()
        )
    else:
        distributions = distributions.filter(hapa__isnull=True)
        jours_avec_distribution = set(
            distributions.values_list("jour", flat=True).distinct()
        )

    resultats = {cible: [] for cible in (cibles_ids if par_hapa else [None])}
    jour = date_min
    while jour <= date_max:
        changements_du_jour = changements.get(jour, {})
        for cible in resultats:
            variations = (
                changements_du_jour.get(cible, {})
                if par_hapa
                else changements_du_jour
            )
            for espece, variation in variations.items():
                effectifs[cible][espece] = (
                    effectifs[cible].get(espece, 0) + variation
                )
            a_distribution = (
                (cible, jour) in jours_avec_distribution
                if par_hapa
                else jour in jours_avec_distribution
            )
            if sum(effectifs[cible].values()) > 0 and not a_distribution:
                resultats[cible].append(jour)
        jour += timedelta(days=1)

    if hapa_id is not None:
        return resultats[hapa_id]
    if par_hapa:
        noms = {hapa.pk: hapa.nom for hapa in cibles_hapas}
        return [
            {
                "id_hapa": cible,
                "nom_hapa": noms[cible],
                "jours_manquants": jours,
            }
            for cible, jours in resultats.items()
        ]
    return resultats[None]