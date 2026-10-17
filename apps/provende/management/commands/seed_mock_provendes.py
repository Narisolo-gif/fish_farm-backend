# apps/provende/management/commands/seed_mock_provendes.py

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.provende.models import Provende, TypeProvende


class Command(BaseCommand):
    help = "Crée cinq provendes fictives pour les tests."

    def handle(self, *args, **options):
        # Une provende de test par type prédéfini.
        mocks = [
            {
                "type": "TRAITEMENT",
                "reference": "PRO-TRA-20260115",
                "date_creation": date(2026, 1, 15),
                "remarques": "Aliment utilisé pendant les traitements sanitaires.",
            },
            {
                "type": "DEMARRAGE",
                "reference": "PRO-DEM-20260203",
                "date_creation": date(2026, 2, 3),
                "remarques": "Granulés fins adaptés aux alevins.",
            },
            {
                "type": "CROISSANCE",
                "reference": "PRO-CRO-20260318",
                "date_creation": date(2026, 3, 18),
                "remarques": "Aliment pour favoriser la croissance des poissons.",
            },
            {
                "type": "FINITION",
                "reference": "PRO-FIN-20260407",
                "date_creation": date(2026, 4, 7),
                "remarques": "Aliment destiné à la phase finale d'élevage.",
            },
            {
                "type": "GENITEURS",
                "reference": "PRO-GEN-20260522",
                "date_creation": date(2026, 5, 22),
                "remarques": "Aliment destiné aux poissons reproducteurs.",
            },
        ]

        for mock in mocks:
            # Le type doit exister : le script ne crée pas les types lui-même.
            try:
                type_provende = TypeProvende.objects.get(type=mock["type"])
            except TypeProvende.DoesNotExist as exc:
                raise CommandError(
                    f"Le type prédéfini {mock['type']} est absent. "
                    "Exécutez d'abord les migrations."
                ) from exc

            # get_or_create rend l'opération idempotente sur la référence.
            provende, created = Provende.objects.get_or_create(
                reference=mock["reference"],
                defaults={
                    "type_provende": type_provende,
                    "date_creation": mock["date_creation"],
                    "remarques": mock["remarques"],
                },
            )

            statut = "Créée" if created else "Déjà existante"
            self.stdout.write(f"{statut} : {provende.reference}")

        self.stdout.write(
            self.style.SUCCESS("Traitement des cinq mocks terminé.")
        )