from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.effectifs.models import MouvementEffectif
from apps.effectifs.services import (
    enregistrer_mouvement_operation,
    operation_a_mouvements,
)
from apps.reproduction.models import Reproduction
from apps.stocks.models import MiseEnStock
from apps.traitement.models import Traitement


class Command(BaseCommand):
    help = (
        "Crée des mouvements d’ouverture à la date de bascule pour les "
        "reproductions actives, les lots en traitement et les lots en stock."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            dest="date_bascule",
            help="Date YYYY-MM-DD des mouvements d’ouverture (défaut : aujourd’hui).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compte les opérations à reprendre sans écrire de mouvements.",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            help="Identifiant de l’utilisateur qui tracera les mouvements.",
        )

    def handle(self, *args, **options):
        date_bascule = options["date_bascule"]
        if date_bascule:
            try:
                date_bascule = date.fromisoformat(date_bascule)
            except ValueError as exc:
                raise CommandError(
                    "--date doit utiliser le format YYYY-MM-DD."
                ) from exc
        else:
            date_bascule = timezone.localdate()

        if date_bascule > timezone.localdate():
            raise CommandError("La date de bascule ne peut pas être dans le futur.")

        operations = []

        reproductions = Reproduction.objects.filter(
            statut=Reproduction.Statut.ACTIF,
        ).select_related("bassin")
        for reproduction in reproductions:
            operations.append((
                reproduction,
                reproduction.bassin,
                reproduction.espece,
                reproduction.quantite_male + reproduction.quantite_femelle,
                None,
            ))

        treatments = (
            Traitement.objects
            .select_related("lot", "bassin")
            .filter(lot__mise_en_stock__isnull=True)
        )
        for treatment in treatments:
            quantite = (
                treatment.effectif_final
                if treatment.effectif_final is not None
                else treatment.effectif_initial
            )
            if quantite > 0:
                operations.append((
                    treatment,
                    treatment.bassin,
                    treatment.lot.espece,
                    quantite,
                    treatment.lot,
                ))

        stock_records = (
            MiseEnStock.objects
            .select_related("lot", "bassin")
            .all()
        )
        for stock_record in stock_records:
            operations.append((
                stock_record,
                stock_record.bassin,
                stock_record.lot.espece,
                stock_record.effectif_entrant,
                stock_record.lot,
            ))

        to_create = [
            operation_data
            for operation_data in operations
            if not operation_a_mouvements(operation_data[0])
        ]
        skipped = len(operations) - len(to_create)

        if options["dry_run"]:
            self.stdout.write(
                f"Date de bascule : {date_bascule}. "
                f"Mouvements à créer : {len(to_create)} ; déjà repris : {skipped}."
            )
            return

        if not options["user_id"]:
            raise CommandError("--user-id est obligatoire hors mode --dry-run.")

        User = get_user_model()
        try:
            user = User.objects.get(pk=options["user_id"])
        except User.DoesNotExist as exc:
            raise CommandError("Utilisateur de traçabilité introuvable.") from exc

        created = 0
        for operation, bassin, espece, quantite, lot in to_create:
            enregistrer_mouvement_operation(
                operation=operation,
                bassin=bassin,
                espece=espece,
                type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
                quantite=quantite,
                date_evenement=date_bascule,
                saisi_par=user,
                lot_origine=lot,
            )
            created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Reprise terminée à la date {date_bascule} : "
                f"{created} mouvement(s) créé(s), {skipped} déjà repris."
            )
        )
