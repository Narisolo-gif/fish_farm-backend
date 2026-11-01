from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from rest_framework.exceptions import NotFound, ValidationError

from apps.bassins.models import Bassin, Hapa
from apps.effectifs.calculs import (
    bassin_a_des_hapas,
    effectifs_du_bassin,
    effectifs_par_hapa,
)
from apps.effectifs.models import MouvementEffectif
from apps.effectifs.services import (
    annuler_mouvement,
    enregistrer_mouvement_operation,
)
from apps.lots.models import Lot

from .models import Traitement, TraitementHapa


@transaction.atomic
def creer_traitement(validated_data, utilisateur):
    """Crée un traitement et ses entrées d’effectif."""
    validated_data = dict(validated_data)
    hapas_data = validated_data.pop("hapas", None)

    lot = Lot.objects.select_for_update().get(
        pk=validated_data["lot"].pk
    )
    bassin = Bassin.objects.select_for_update().get(
        pk=validated_data["bassin"].pk
    )

    if lot.parcours_production == Lot.ParcoursProduction.SANS_TRAITEMENT:
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

    hapas = []
    if bassin_a_des_hapas(bassin.pk):
        if not hapas_data:
            raise ValidationError({
                "hapas": "Les hapas et leurs effectifs initiaux sont obligatoires."
            })

        hapa_ids = [ligne["id_hapa"] for ligne in hapas_data]
        if len(hapa_ids) != len(set(hapa_ids)):
            raise ValidationError({
                "hapas": "Un hapa ne peut être indiqué qu'une seule fois."
            })

        hapas = list(
            Hapa.objects.select_for_update()
            .filter(pk__in=sorted(hapa_ids))
            .order_by("pk")
        )
        hapas_by_id = {hapa.pk: hapa for hapa in hapas}
        if len(hapas_by_id) != len(hapa_ids):
            raise ValidationError({
                "hapas": "Un ou plusieurs hapas sont introuvables."
            })

        effectifs = effectifs_par_hapa(
            bassin.pk,
            a_la_date=validated_data["date_ouverture"],
        )
        for ligne in hapas_data:
            hapa = hapas_by_id[ligne["id_hapa"]]
            if hapa.bassin_id != bassin.pk:
                raise ValidationError({
                    "hapas": "Chaque hapa doit appartenir au bassin du traitement."
                })
            if not hapa.actif or hapa.statut != Hapa.Statut.ACTIF:
                raise ValidationError({
                    "hapas": f"Le hapa {hapa.nom} est inactif."
                })
            if ligne["effectif_initial"] <= 0:
                raise ValidationError({
                    "hapas": "L’effectif initial de chaque hapa doit être positif."
                })
            if any(
                effectif != 0
                for (hapa_id, _), effectif in effectifs.items()
                if hapa_id == hapa.pk
            ):
                raise ValidationError({
                    "hapas": f"Le hapa {hapa.nom} doit être vide à la date d’ouverture."
                })
            if TraitementHapa.objects.filter(
                hapa=hapa,
                traitement__statut=Traitement.Statut.EN_COURS,
            ).exists():
                raise ValidationError({
                    "hapas": f"Le hapa {hapa.nom} est déjà utilisé par un traitement en cours."
                })

        if sum(ligne["effectif_initial"] for ligne in hapas_data) != validated_data[
            "effectif_initial"
        ]:
            raise ValidationError({
                "hapas": "La somme des effectifs initiaux par hapa doit égaler l’effectif initial du traitement."
            })
    elif hapas_data is not None:
        raise ValidationError({
            "hapas": "Les hapas ne sont pas autorisés pour un bassin sans hapa actif."
        })

    validated_data["bassin"] = bassin
    validated_data["utilisateur"] = utilisateur
    validated_data["statut"] = Traitement.Statut.EN_COURS
    validated_data["effectif_final"] = None
    validated_data["date_cloture"] = None
    traitement = Traitement.objects.create(**validated_data)

    if hapas_data is not None:
        lignes_hapas = []
        hapas_by_id = {hapa.pk: hapa for hapa in hapas}
        for ligne in hapas_data:
            hapa = hapas_by_id[ligne["id_hapa"]]
            ligne_traitement = TraitementHapa.objects.create(
                traitement=traitement,
                hapa=hapa,
                effectif_initial=ligne["effectif_initial"],
            )
            lignes_hapas.append(ligne_traitement)

        for ligne in lignes_hapas:
            enregistrer_mouvement_operation(
                operation=traitement,
                bassin=bassin,
                hapa=ligne.hapa,
                espece=lot.espece,
                type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
                quantite=ligne.effectif_initial,
                date_evenement=traitement.date_ouverture,
                saisi_par=utilisateur,
                lot_origine=lot,
            )
    else:
        enregistrer_mouvement_operation(
            operation=traitement,
            bassin=bassin,
            espece=lot.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            quantite=traitement.effectif_initial,
            date_evenement=traitement.date_ouverture,
            saisi_par=utilisateur,
            lot_origine=lot,
        )

    lot.stade = Lot.Stade.EN_TRAITEMENT
    lot.save(update_fields=["stade", "updated_at"])
    return traitement


def effectifs_traitement(traitement):
    lignes = list(
        traitement.lignes_hapas.select_related("hapa").order_by("hapa_id")
    )
    if not lignes:
        return []

    effectifs = effectifs_par_hapa(
        traitement.bassin_id,
        a_la_date=timezone.localdate(),
    )
    espece = traitement.lot.espece
    return [
        {
            "id_hapa": ligne.hapa_id,
            "nom_hapa": ligne.hapa.nom,
            "effectif_initial": ligne.effectif_initial,
            "effectif_actuel": effectifs.get((ligne.hapa_id, espece), 0),
            "effectif_final": ligne.effectif_final,
        }
        for ligne in lignes
    ]


@transaction.atomic
def cloturer_traitement(traitement_id):
    """Calcule puis fige les effectifs du traitement à la clôture."""
    try:
        traitement = Traitement.objects.select_for_update().get(
            pk=traitement_id
        )
    except Traitement.DoesNotExist as exc:
        raise NotFound("Traitement introuvable.") from exc

    if traitement.statut == Traitement.Statut.CLOTUREE:
        raise ValidationError({"detail": "Ce traitement est déjà clôturé."})

    lot = Lot.objects.select_for_update().get(pk=traitement.lot_id)
    bassin = Bassin.objects.select_for_update().get(pk=traitement.bassin_id)
    date_cloture = timezone.localdate()
    lignes = list(
        TraitementHapa.objects.select_for_update()
        .filter(traitement=traitement)
        .select_related("hapa")
        .order_by("hapa_id")
    )

    if lignes:
        list(
            Hapa.objects.select_for_update()
            .filter(pk__in=[ligne.hapa_id for ligne in lignes])
            .order_by("pk")
        )
        effectifs = effectifs_par_hapa(
            bassin.pk,
            a_la_date=date_cloture,
        )
        effectifs_final = {}
        for ligne in lignes:
            courant = effectifs.get((ligne.hapa_id, lot.espece), 0)
            if courant > ligne.effectif_initial:
                raise ValidationError({
                    "effectif_final": (
                        f"L’effectif calculé du hapa {ligne.hapa.nom} ({courant}) "
                        f"dépasse son effectif initial ({ligne.effectif_initial})."
                    )
                })
            effectifs_final[ligne.pk] = courant
        effectif_final = sum(effectifs_final.values())
    else:
        autre_traitement = Traitement.objects.filter(
            bassin=bassin,
            statut=Traitement.Statut.EN_COURS,
            lot__espece=lot.espece,
        ).exclude(pk=traitement.pk).exists()
        if autre_traitement:
            raise ValidationError({
                "effectif_final": (
                    "Effectif non calculable sans hapas : un autre traitement "
                    "de la même espèce est en cours dans ce bassin."
                )
            })

        effectif_final = effectifs_du_bassin(
            bassin.pk,
            a_la_date=date_cloture,
        ).get(lot.espece, 0)
        if effectif_final > traitement.effectif_initial:
            raise ValidationError({
                "effectif_final": (
                    f"L’effectif calculé ({effectif_final}) dépasse "
                    f"l’effectif initial ({traitement.effectif_initial})."
                )
            })

    for ligne in lignes:
        ligne.effectif_final = effectifs_final[ligne.pk]
        ligne.save(update_fields=["effectif_final"])

    traitement.effectif_final = effectif_final
    traitement.date_cloture = date_cloture
    traitement.statut = Traitement.Statut.CLOTUREE
    traitement.save(update_fields=[
        "effectif_final",
        "date_cloture",
        "statut",
        "updated_at",
    ])
    return traitement


@transaction.atomic
def supprimer_traitement(traitement_id):
    """Annule les entrées du traitement puis remet son lot à l’état CREEE."""
    try:
        traitement = Traitement.objects.select_for_update().get(
            pk=traitement_id
        )
    except Traitement.DoesNotExist as exc:
        raise NotFound("Traitement introuvable.") from exc

    lot = Lot.objects.select_for_update().get(pk=traitement.lot_id)

    if hasattr(lot, "mise_en_stock"):
        raise ValidationError({
            "detail": (
                "Ce traitement ne peut pas être supprimé, car le lot "
                "possède déjà une mise en stock."
            )
        })

    lignes = list(
        TraitementHapa.objects.select_for_update()
        .filter(traitement=traitement)
        .order_by("hapa_id")
    )
    bassin = Bassin.objects.select_for_update().get(pk=traitement.bassin_id)
    if lignes:
        list(
            Hapa.objects.select_for_update()
            .filter(pk__in=[ligne.hapa_id for ligne in lignes])
            .order_by("pk")
        )
    mouvements = MouvementEffectif.objects.filter(bassin=bassin)
    if lignes:
        mouvements = mouvements.filter(
            hapa_id__in=[ligne.hapa_id for ligne in lignes]
        )
    else:
        mouvements = mouvements.filter(hapa__isnull=True)

    content_type = ContentType.objects.get_for_model(Traitement)
    mouvements_a_supprimer = (
        mouvements
        .exclude(type_mouvement=MouvementEffectif.TypeMouvement.ANNULATION)
        .filter(annulations_associees__isnull=True)
        .exclude(
            operation_source_content_type=content_type,
            operation_source_object_id=traitement.pk,
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
        )
    )
    if mouvements_a_supprimer.exists():
        raise ValidationError({
            "detail": (
                "Ce traitement ne peut pas être supprimé, car un mouvement "
                "non annulé existe sur le bassin ou ses hapas."
            )
        })

    # TODO: refuser aussi s'il existe des distributions de provende (étape provende).
    entrees = MouvementEffectif.objects.filter(
        operation_source_content_type=content_type,
        operation_source_object_id=traitement.pk,
        type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
        annulations_associees__isnull=True,
    )
    for mouvement in entrees:
        annuler_mouvement(
            mouvement.pk,
            motif_annulation="Suppression du traitement",
            saisi_par=traitement.utilisateur,
        )

    try:
        TraitementHapa.objects.filter(traitement=traitement).delete()
        traitement.delete()
    except ProtectedError as exc:
        raise ValidationError({
            "detail": (
                "Ce traitement est référencé par d'autres données "
                "et ne peut pas être supprimé."
            )
        }) from exc

    lot.stade = Lot.Stade.CREEE
    lot.save(update_fields=["stade", "updated_at"])
