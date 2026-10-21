from collections import defaultdict
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db.models import ExpressionWrapper, F, IntegerField, Sum
from django.utils import timezone

from apps.effectifs.models import MouvementEffectif
from apps.reproduction.models import Reproduction
from apps.stocks.models import MiseEnStock
from apps.traitement.models import Traitement


class Command(BaseCommand):
    help = (
        "Compare les effectifs présents dans les opérations existantes "
        "avec les soldes du registre par bassin et espèce."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            dest="date_reference",
            help="Date YYYY-MM-DD de comparaison (défaut : aujourd’hui).",
        )

    def handle(self, *args, **options):
        date_reference = options["date_reference"]
        if date_reference:
            try:
                date_reference = date.fromisoformat(date_reference)
            except ValueError as exc:
                raise CommandError("--date doit utiliser le format YYYY-MM-DD.") from exc
        else:
            date_reference = timezone.localdate()

        if date_reference > timezone.localdate():
            raise CommandError("La date de comparaison ne peut pas être dans le futur.")

        attendu = defaultdict(int)
        references = {}

        reproductions = Reproduction.objects.filter(
            statut=Reproduction.Statut.ACTIF,
            date_ajout__lte=date_reference,
        ).values(
            "bassin_id",
            "bassin__reference",
            "espece",
            "quantite_male",
            "quantite_femelle",
        )
        for row in reproductions:
            key = (row["bassin_id"], row["espece"])
            attendu[key] += (
                row["quantite_male"] + row["quantite_femelle"]
            )
            references[key] = row["bassin__reference"]

        stocked_lot_ids = list(
            MiseEnStock.objects.filter(
                date_mise_en_stock__date__lte=date_reference,
            ).values_list("lot_id", flat=True)
        )

        treatments = (
            Traitement.objects
            .filter(date_ouverture__lte=date_reference)
            .exclude(lot_id__in=stocked_lot_ids)
            .values(
                "bassin_id",
                "bassin__reference",
                "lot__espece",
                "effectif_initial",
                "effectif_final",
            )
        )
        for row in treatments:
            quantite = (
                row["effectif_final"]
                if row["effectif_final"] is not None
                else row["effectif_initial"]
            )
            key = (row["bassin_id"], row["lot__espece"])
            attendu[key] += quantite
            references[key] = row["bassin__reference"]

        stocks = MiseEnStock.objects.filter(
            date_mise_en_stock__date__lte=date_reference,
        ).values(
            "bassin_id",
            "bassin__reference",
            "lot__espece",
            "effectif_entrant",
        )
        for row in stocks:
            key = (row["bassin_id"], row["lot__espece"])
            attendu[key] += (
                row["effectif_entrant"]
            )
            references[key] = row["bassin__reference"]

        enregistrer = {}
        totals = (
            MouvementEffectif.objects
            .filter(date_evenement__lte=date_reference)
            .annotate(
                contribution=ExpressionWrapper(
                    F("sens") * F("quantite"),
                    output_field=IntegerField(),
                )
            )
            .values("bassin_id", "bassin__reference", "espece")
            .annotate(solde=Sum("contribution"))
        )
        for row in totals:
            key = (row["bassin_id"], row["espece"])
            enregistrer[key] = row["solde"] or 0
            references[key] = row["bassin__reference"]

        keys = set(attendu) | set(enregistrer)
        differences = []
        for bassin_id, espece in sorted(keys):
            expected = attendu[(bassin_id, espece)]
            balance = enregistrer.get((bassin_id, espece), 0)
            if expected != balance:
                differences.append((
                    references.get((bassin_id, espece), str(bassin_id)),
                    espece,
                    expected,
                    balance,
                    balance - expected,
                ))

        self.stdout.write(f"Réconciliation au {date_reference}")
        self.stdout.write(
            "Bassin | Espèce | Opérations | Registre | Écart registre - opérations"
        )
        if not differences:
            self.stdout.write(self.style.SUCCESS("Aucun écart détecté."))
        else:
            for row in differences:
                self.stdout.write(
                    f"{row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]}"
                )
            self.stdout.write(
                self.style.WARNING(
                    f"{len(differences)} combinaison(s) bassin/espèce en écart."
                )
            )

        inactive_reproductions = Reproduction.objects.filter(
            statut=Reproduction.Statut.INACTIF,
        ).count()
        if inactive_reproductions:
            self.stdout.write(
                self.style.WARNING(
                    f"{inactive_reproductions} reproduction(s) inactive(s) "
                    "exclue(s) des totaux attendus : leur sortie physique "
                    "n’est pas encore enregistrée."
                )
            )
