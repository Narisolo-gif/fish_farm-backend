from django.db.models import ExpressionWrapper, F, IntegerField, Sum
from django.utils import timezone

from .models import MouvementEffectif

HAPA_OBLIGATOIRE = True


def bassin_a_des_hapas(bassin_id):
    from apps.bassins.models import Hapa

    return Hapa.objects.filter(
        bassin_id=bassin_id,
        actif=True,
        statut="ACTIF",
    ).exists()


def effectif_vivant(bassin_id, espece, a_la_date=None, hapa_id=None):
    date_reference = a_la_date or timezone.localdate()
    mouvements = MouvementEffectif.objects.filter(
        bassin_id=bassin_id,
        espece=espece,
        date_evenement__lte=date_reference,
    )
    if hapa_id is not None:
        mouvements = mouvements.filter(hapa_id=hapa_id)
    contributions = mouvements.annotate(
        contribution=ExpressionWrapper(
            F("sens") * F("quantite"),
            output_field=IntegerField(),
        )
    ).aggregate(effectif=Sum("contribution"))
    return contributions["effectif"] or 0


def effectifs_du_bassin(bassin_id, a_la_date=None):
    """Retourne les effectifs vivants par espèce dans un bassin à une date."""
    date_reference = a_la_date or timezone.localdate()
    contributions = (
        MouvementEffectif.objects
        .filter(bassin_id=bassin_id, date_evenement__lte=date_reference)
        .annotate(
            contribution=ExpressionWrapper(
                F("sens") * F("quantite"),
                output_field=IntegerField(),
            )
        )
        .values("espece")
        .annotate(effectif=Sum("contribution"))
    )
    return {
        row["espece"]: row["effectif"] or 0
        for row in contributions
    }


def effectifs_par_hapa(bassin_id, a_la_date=None):
    date_reference = a_la_date or timezone.localdate()
    contributions = (
        MouvementEffectif.objects
        .filter(bassin_id=bassin_id, hapa__isnull=False, date_evenement__lte=date_reference)
        .annotate(
            contribution=ExpressionWrapper(
                F("sens") * F("quantite"),
                output_field=IntegerField(),
            )
        )
        .values("hapa_id", "espece")
        .annotate(effectif=Sum("contribution"))
    )
    return {
        (row["hapa_id"], row["espece"]): row["effectif"] or 0
        for row in contributions
    }


def lots_presents_dans_hapa(hapa_id, a_la_date=None):
    date_reference = a_la_date or timezone.localdate()
    contributions = (
        MouvementEffectif.objects
        .filter(
            hapa_id=hapa_id,
            lot_origine__isnull=False,
            date_evenement__lte=date_reference,
            type_mouvement__in=[
                MouvementEffectif.TypeMouvement.ENTREE,
                MouvementEffectif.TypeMouvement.SORTIE,
                MouvementEffectif.TypeMouvement.VENTE,
                MouvementEffectif.TypeMouvement.ANNULATION,
            ],
        )
        .annotate(
            contribution=ExpressionWrapper(
                F("sens") * F("quantite"),
                output_field=IntegerField(),
            )
        )
        .values("lot_origine_id")
        .annotate(restant=Sum("contribution"))
    )
    return {
        row["lot_origine_id"]: row["restant"]
        for row in contributions
        if row["restant"] and row["restant"] > 0
    }