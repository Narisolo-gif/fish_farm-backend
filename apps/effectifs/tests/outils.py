"""Outils partagés par les tests (pas de code applicatif ici)."""
from datetime import datetime, time

from django.utils import timezone

from apps.effectifs.services import enregistrer_evenement


def enregistrer_mortalite_perte(
    *,
    bassin,
    espece,
    type_evenement,
    quantite,
    date_evenement,
    saisi_par,
    hapa=None,
    cause_mortalite=None,
    categorie_perte=None,
    justification="",
):
    """Enregistre une mortalité ou une perte par le service du journal.

    Accepte une date simple (convertie à minuit, heure locale) pour alléger
    l'écriture des tests.
    """
    if not isinstance(date_evenement, datetime):
        date_evenement = timezone.make_aware(
            datetime.combine(date_evenement, time.min),
            timezone.get_current_timezone(),
        )
    return enregistrer_evenement(
        validated_data={
            "bassin": bassin,
            "hapa": hapa,
            "espece": espece,
            "type_evenement": type_evenement,
            "quantite": quantite,
            "date_evenement": date_evenement,
            "cause_mortalite": cause_mortalite,
            "categorie_perte": categorie_perte,
            "justification": justification,
        },
        utilisateur=saisi_par,
    )
