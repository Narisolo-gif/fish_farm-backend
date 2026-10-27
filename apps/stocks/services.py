
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from rest_framework.exceptions import NotFound, ValidationError

from apps.bassins.models import Bassin
from apps.effectifs.models import MouvementEffectif
from apps.effectifs.services import (
    _verifier_hapa,
    enregistrer_mouvement_operation,
    operation_a_mouvements,
)
from apps.lots.models import Lot
from apps.traitement.models import Traitement

from .models import MiseEnStock


def _verifier_bassin_stock(bassin):
    """Vérifie que le bassin de destination peut recevoir le lot."""
    if bassin.statut != Bassin.Statut.ACTIF:
        raise ValidationError({
            "id_bassin": "Le bassin sélectionné est inactif."
        })

    if bassin.categorie != Bassin.Categorie.STOCKS:
        raise ValidationError({
            "id_bassin": "Le bassin doit être de catégorie STOCKS."
        })


def _verifier_justification(lot, effectif_entrant, justification):
    """Exige une justification lorsqu'un écart doit être expliqué."""
    traitement = getattr(lot, "traitement", None)

    if traitement is not None:
        if traitement.statut != Traitement.Statut.CLOTUREE:
            raise ValidationError({
                "id_lot": (
                    "Le traitement doit être clôturé avant la mise en stock."
                )
            })

        # Un traitement clôturé doit disposer de son effectif final.
        if traitement.effectif_final is None:
            raise ValidationError({
                "id_lot": "L'effectif final du traitement est manquant."
            })

        ecart = effectif_entrant - traitement.effectif_final
    else:
        # Pour un lot sans traitement, la référence est la quantité initiale.
        ecart = effectif_entrant - lot.quantite_initiale

    if ecart != 0 and not (justification or "").strip():
        raise ValidationError({
            "justification_ecart": (
                "Une justification est obligatoire en cas d'écart d'effectif."
            )
        })
        
def _verifier_effectif_entrant(lot, effectif_entrant):
    """
    Vérifie que l'effectif entrant ne dépasse jamais
    l'effectif de référence autorisé pour ce lot.
    """
    traitement = getattr(lot, "traitement", None)

    if traitement is not None:
        # Un lot traité doit avoir un traitement clôturé.
        if traitement.statut != Traitement.Statut.CLOTUREE:
            raise ValidationError({
                "id_lot": "Le traitement doit être clôturé avant la mise en stock."
            })

        # Impossible de valider un effectif sans effectif final connu.
        if traitement.effectif_final is None:
            raise ValidationError({
                "effectif_entrant": (
                    "L'effectif final du traitement doit être renseigné."
                )
            })

        effectif_reference = traitement.effectif_final

    else:
        # Sans traitement, la référence est la quantité initiale du lot.
        effectif_reference = lot.quantite_initiale

    # Une justification d'écart ne permet jamais de dépasser la référence.
    if effectif_entrant > effectif_reference:
        raise ValidationError({
            "effectif_entrant": (
                f"L'effectif entrant ({effectif_entrant}) ne peut pas "
                f"dépasser l'effectif de référence ({effectif_reference})."
            )
        })

@transaction.atomic
def creer_mise_en_stock(validated_data, utilisateur):
    """Enregistre l'entrée en stock et fait passer le lot à EN_STOCK."""

    # Le verrou empêche deux opérations concurrentes sur le même lot.
    lot = Lot.objects.select_for_update().get(
        pk=validated_data["lot"].pk
    )

    # La relation OneToOne protège aussi contre les doublons en base.
    if MiseEnStock.objects.filter(lot=lot).exists():
        raise ValidationError({
            "id_lot": "Ce lot possède déjà une mise en stock."
        })

    # Un lot clôturé ne doit pas revenir dans le circuit de mise en stock.
    if lot.stade not in [
        Lot.Stade.CREEE,
        Lot.Stade.EN_TRAITEMENT,
    ]:
        raise ValidationError({
            "id_lot": (
                f"Le lot ne peut pas être mis en stock depuis le stade "
                f"{lot.get_stade_display()}."
            )
        })

    # Si le lot a un traitement, celui-ci doit être clôturé.
    traitement = getattr(lot, "traitement", None)
    if traitement is not None and traitement.statut != Traitement.Statut.CLOTUREE:
        raise ValidationError({
            "id_lot": "Le traitement doit être clôturé avant la mise en stock."
        })

    bassin = validated_data["bassin"]
    _verifier_bassin_stock(bassin)
    hapa = validated_data.get("hapa")
    _verifier_hapa(bassin, hapa, champ="id_hapa")

    effectif = validated_data["effectif_entrant"]
    justification = validated_data.get("justification_ecart", "")

    _verifier_effectif_entrant(lot, effectif)
    _verifier_justification(lot, effectif, justification)
    if (
        traitement is not None
        and effectif != traitement.effectif_final
    ):
        raise ValidationError({
            "effectif_entrant": (
                "La mise en stock doit correspondre à l’effectif final du "
                "traitement. Les écarts nécessitent un événement de perte "
                "ou de mortalité."
            )
        })

    # Les champs d'identité sont contrôlés côté serveur.
    validated_data["lot"] = lot
    validated_data["utilisateur"] = utilisateur

    mise_en_stock = MiseEnStock.objects.create(**validated_data)

    date_evenement = timezone.localtime(
        mise_en_stock.date_mise_en_stock
    ).date()

    if traitement is not None:
        enregistrer_mouvement_operation(
            operation=mise_en_stock,
            bassin=traitement.bassin,
            espece=lot.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
            quantite=traitement.effectif_final,
            date_evenement=date_evenement,
            saisi_par=utilisateur,
            lot_origine=lot,
        )

    enregistrer_mouvement_operation(
        operation=mise_en_stock,
        bassin=bassin,
        espece=lot.espece,
        type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
        quantite=effectif,
        date_evenement=date_evenement,
        saisi_par=utilisateur,
        lot_origine=lot,
        hapa=hapa,
    )

    # Le changement de stade et la création sont dans la même transaction.
    lot.stade = Lot.Stade.EN_STOCK
    lot.save(update_fields=["stade", "updated_at"])

    return mise_en_stock


@transaction.atomic
def modifier_mise_en_stock(mise_en_stock_id, validated_data):
    """Modifie une mise en stock sans changer son lot de rattachement."""

    try:
        mise = MiseEnStock.objects.select_for_update().get(
            pk=mise_en_stock_id
        )
    except MiseEnStock.DoesNotExist as exc:
        raise NotFound("Mise en stock introuvable.") from exc

    # Le verrou du lot protège également son cycle de vie.
    lot = Lot.objects.select_for_update().get(pk=mise.lot_id)

    if operation_a_mouvements(mise):
        raise ValidationError({
            "detail": (
                "Une mise en stock référencée par le registre ne peut pas "
                "être modifiée."
            )
        })

    if lot.stade != Lot.Stade.EN_STOCK:
        raise ValidationError({
            "detail": "Cette mise en stock ne peut plus être modifiée."
        })

    bassin = validated_data.get("bassin", mise.bassin)
    hapa = validated_data.get("hapa", mise.hapa)
    effectif = validated_data.get(
        "effectif_entrant",
        mise.effectif_entrant,
    )
    justification = validated_data.get(
        "justification_ecart",
        mise.justification_ecart,
    )

    _verifier_bassin_stock(bassin)
    _verifier_hapa(bassin, hapa, champ="id_hapa")
    _verifier_justification(lot, effectif, justification)

    # Seuls les champs modifiables sont appliqués.
    for champ in ("bassin", "hapa", "effectif_entrant", "justification_ecart", "observation"):
        if champ in validated_data:
            setattr(mise, champ, validated_data[champ])

    mise.save()
    return mise


@transaction.atomic
def supprimer_mise_en_stock(mise_en_stock_id):
    """Supprime l'opération et rétablit le stade antérieur du lot."""

    try:
        mise = MiseEnStock.objects.select_for_update().get(
            pk=mise_en_stock_id
        )
    except MiseEnStock.DoesNotExist as exc:
        raise NotFound("Mise en stock introuvable.") from exc

    lot = Lot.objects.select_for_update().get(pk=mise.lot_id)

    if operation_a_mouvements(mise):
        raise ValidationError({
            "detail": (
                "Une mise en stock référencée par le registre ne peut pas "
                "être supprimée."
            )
        })

    # Ne pas réinitialiser un lot qui a déjà progressé au-delà du stock.
    if lot.stade != Lot.Stade.EN_STOCK:
        raise ValidationError({
            "detail": (
                "La mise en stock ne peut pas être supprimée : "
                "le lot n'est plus au stade EN_STOCK."
            )
        })

    try:
        mise.delete()
    except ProtectedError as exc:
        raise ValidationError({
            "detail": (
                "Cette mise en stock est référencée par d'autres données "
                "et ne peut pas être supprimée."
            )
        }) from exc

    # Le lot revient à l'étape qu'il occupait avant la mise en stock.
    traitement = getattr(lot, "traitement", None)
    lot.stade = (
        Lot.Stade.EN_TRAITEMENT
        if traitement is not None
        else Lot.Stade.CREEE
    )
    lot.save(update_fields=["stade", "updated_at"])
