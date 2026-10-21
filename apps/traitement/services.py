from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from rest_framework.exceptions import NotFound, ValidationError

from apps.effectifs.models import MouvementEffectif
from apps.effectifs.services import (
    enregistrer_mouvement_operation,
    operation_a_mouvements,
)
from apps.lots.models import Lot

from .models import Traitement


@transaction.atomic
def creer_traitement(validated_data, utilisateur):
    """Crée un traitement et fait passer le lot à EN_TRAITEMENT."""

    # Verrouille le lot pendant la création pour réduire les courses concurrentes.
    lot = Lot.objects.select_for_update().get(
        pk=validated_data["lot"].pk
    )

    if (
        lot.parcours_production
        == Lot.ParcoursProduction.SANS_TRAITEMENT
    ):
        raise ValidationError({
            "id_lot": "Ce lot a été déclaré sans traitement."
        })

    if Traitement.objects.filter(lot=lot).exists():
        raise ValidationError({
            "id_lot": "Ce lot possède déjà un traitement."
        })

    if lot.stade != Lot.Stade.CREEE:
        raise ValidationError({
            "id_lot": "Seul un lot au stade CREEE peut être mis en traitement."
        })

    # Les champs contrôlés par le serveur ne viennent pas du client.
    validated_data["utilisateur"] = utilisateur
    validated_data["statut"] = Traitement.Statut.EN_COURS
    validated_data["effectif_final"] = None
    validated_data["date_cloture"] = None

    traitement = Traitement.objects.create(**validated_data)

    # Le traitement et le changement de stade sont validés ensemble.
    lot.stade = Lot.Stade.EN_TRAITEMENT
    lot.save(update_fields=["stade", "updated_at"])

    enregistrer_mouvement_operation(
        operation=traitement,
        bassin=traitement.bassin,
        espece=lot.espece,
        type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
        quantite=traitement.effectif_initial,
        date_evenement=traitement.date_ouverture,
        saisi_par=utilisateur,
        lot_origine=lot,
    )

    return traitement


@transaction.atomic
def cloturer_traitement(traitement_id, effectif_final):
    """Clôture un traitement et génère sa date de clôture."""

    try:
        traitement = Traitement.objects.select_for_update().get(
            pk=traitement_id
        )
    except Traitement.DoesNotExist as exc:
        raise NotFound("Traitement introuvable.") from exc

    if traitement.statut == Traitement.Statut.CLOTUREE:
        raise ValidationError("Ce traitement est déjà clôturé.")

    # Vérification métier répétée dans le service.
    if not 0 <= effectif_final <= traitement.effectif_initial:
        raise ValidationError({
            "effectif_final": (
                "L'effectif final doit être compris entre zéro "
                "et l'effectif initial."
            )
        })

    if effectif_final != traitement.effectif_initial:
        raise ValidationError({
            "effectif_final": (
                "Jusqu’à l’activation du journal de mortalité/perte, "
                "l’effectif final doit égaler l’effectif initial."
            )
        })

    traitement.effectif_final = effectif_final
    traitement.date_cloture = timezone.localdate()
    traitement.statut = Traitement.Statut.CLOTUREE

    traitement.save(update_fields=[
        "effectif_final",
        "date_cloture",
        "statut",
        "updated_at",
    ])

    # Le stade du lot ne change pas lors de la clôture.
    return traitement


@transaction.atomic
def supprimer_traitement(traitement_id):
    """Supprime le traitement et remet le lot à l'état CREEE."""

    try:
        # Le verrou du lot évite les opérations concurrentes sur celui-ci.
        traitement = Traitement.objects.select_for_update().get(
            pk=traitement_id
        )
    except Traitement.DoesNotExist as exc:
        raise NotFound("Traitement introuvable.") from exc

    lot = Lot.objects.select_for_update().get(
        pk=traitement.lot.pk
    )

    if operation_a_mouvements(traitement):
        raise ValidationError({
            "detail": (
                "Un traitement référencé par le registre ne peut pas être "
                "supprimé."
            )
        })
    
    # Un traitement ne peut pas être supprimé si le lot a déjà été mis en stock.
    # Sinon, la suppression pourrait invalider l'historique de production.
    if hasattr(lot, "mise_en_stock"):
        raise ValidationError({
            "detail": (
                "Ce traitement ne peut pas être supprimé, car le lot "
                "possède déjà une mise en stock."
            )
        })

    try:
        # Si une autre donnée protège le traitement, la transaction sera annulée.
        traitement.delete()
    except ProtectedError as exc:
        raise ValidationError({
            "detail": (
                "Ce traitement est référencé par d'autres données "
                "et ne peut pas être supprimé."
            )
        }) from exc

    # Règle métier retenue : réinitialisation systématique à CREEE.
    lot.stade = Lot.Stade.CREEE
    lot.save(update_fields=["stade", "updated_at"])