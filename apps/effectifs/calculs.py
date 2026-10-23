from django.db.models import ExpressionWrapper, F, IntegerField, Sum
from django.utils import timezone

from .models import MouvementEffectif


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