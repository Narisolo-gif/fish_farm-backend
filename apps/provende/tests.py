from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.bassins.models import Bassin
from apps.core.models import Role
from apps.effectifs.models import MouvementEffectif

from .models import ConsommationProvende, Provende, TypeProvende
from .services import (
    annuler_distribution,
    enregistrer_distribution,
    jours_manquants,
    total_journalier,
)

User = get_user_model()


class ConsommationProvendeTests(TestCase):
    def setUp(self):
        role = Role.objects.create(
            libelle="Administrateur",
            description="Tests de consommation",
        )
        self.user = User.objects.create_user(
            username="provende-admin",
            password="TestPassword123!",
            role=role,
            statut="ACTIF",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.bassin = Bassin.objects.create(
            reference="CONS-001",
            categorie=Bassin.Categorie.STOCKS,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )
        self.type_provende, _ = TypeProvende.objects.get_or_create(
            type=TypeProvende.Type.CROISSANCE,
        )
        self.type_demarrage, _ = TypeProvende.objects.get_or_create(
            type=TypeProvende.Type.DEMARRAGE,
        )
        self.today = timezone.localdate()
        self.provende = self._create_provende(
            self.type_provende,
            "CONS-PROV-001",
        )
        self.provende_demarrage = self._create_provende(
            self.type_demarrage,
            "CONS-PROV-002",
        )
        self._ajouter_effectif(self.bassin, self.today, 20)

    def _create_provende(self, type_provende, reference, date_creation=None):
        return Provende.objects.create(
            type_provende=type_provende,
            reference=reference,
            date_creation=date_creation or self.today,
        )

    def _ajouter_effectif(self, bassin, jour, quantite):
        return MouvementEffectif.objects.create(
            bassin=bassin,
            espece="CARPE",
            type_mouvement="ENTREE",
            sens=1,
            quantite=quantite,
            date_evenement=jour,
            saisi_par=self.user,
        )

    def _datetime_local(self, jour, heure=time(10, 15)):
        return timezone.make_aware(datetime.combine(jour, heure))

    def _creer(self, *, date_distribution=None, provende=None, **overrides):
        data = {
            "id_bassin": self.bassin.pk,
            "id_provende": (provende or self.provende).pk,
            "quantite_kg": "1.25",
        }
        if date_distribution is not None:
            data["date_distribution"] = date_distribution.isoformat()
        data.update(overrides)
        return self.client.post(
            "/api/v1/provende/consommations-provende/",
            data,
            format="json",
        )

    def test_creation_valide_and_same_day_different_times(self):
        first = self._creer(
            date_distribution=self._datetime_local(self.today, time(8, 5)),
        )
        second = self._creer(
            date_distribution=self._datetime_local(self.today, time(16, 40)),
        )

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data["id_bassin"], self.bassin.pk)
        self.assertEqual(first.data["id_provende"], self.provende.pk)
        self.assertEqual(first.data["reference_bassin"], self.bassin.reference)
        self.assertEqual(first.data["reference_provende"], self.provende.reference)
        self.assertEqual(first.data["type_provende"], "Croissance")
        self.assertEqual(first.data["id_user"], self.user.pk)
        self.assertEqual(ConsommationProvende.objects.count(), 2)
        self.assertTrue(
            all(
                row.date_distribution.second == 0
                and row.date_distribution.microsecond == 0
                for row in ConsommationProvende.objects.all()
            )
        )

    def test_duplicate_in_same_minute_is_rejected(self):
        first_time = self._datetime_local(self.today, time(11, 22))
        self.assertEqual(self._creer(date_distribution=first_time).status_code, 201)

        duplicate = self._creer(
            date_distribution=first_time.replace(second=49, microsecond=900),
        )

        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertIn("date_distribution", duplicate.data)
        self.assertEqual(ConsommationProvende.objects.count(), 1)

    def test_resubmission_is_allowed_after_cancellation(self):
        distribution_time = self._datetime_local(self.today, time(12, 10))
        response = self._creer(date_distribution=distribution_time)
        distribution_id = response.data["id"]

        cancelled = annuler_distribution(
            distribution_id,
            motif="Correction du test",
            utilisateur=self.user,
        )
        resubmitted = self._creer(date_distribution=distribution_time)

        self.assertEqual(cancelled.statut, ConsommationProvende.Statut.ANNULEE)
        self.assertEqual(resubmitted.status_code, 201, resubmitted.data)
        self.assertEqual(ConsommationProvende.objects.count(), 2)

    def test_missing_distribution_datetime_defaults_to_now(self):
        before = timezone.now()
        response = self._creer()
        after = timezone.now()

        self.assertEqual(response.status_code, 201, response.data)
        distribution = ConsommationProvende.objects.get(pk=response.data["id"])
        self.assertGreaterEqual(distribution.date_distribution, before.replace(second=0, microsecond=0))
        self.assertLessEqual(distribution.date_distribution, after)

    def test_inactive_basin_is_rejected(self):
        self.bassin.statut = Bassin.Statut.INACTIF
        self.bassin.save(update_fields=["statut"])

        response = self._creer()

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("id_bassin", response.data)
        self.assertFalse(ConsommationProvende.objects.exists())

    def test_future_datetime_is_rejected(self):
        future = timezone.now() + timedelta(minutes=5)

        response = self._creer(date_distribution=future)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("date_distribution", response.data)
        self.assertFalse(ConsommationProvende.objects.exists())

    def test_distribution_before_provende_creation_is_rejected(self):
        future_feed = self._create_provende(
            self.type_provende,
            "CONS-PROV-FUTURE",
            date_creation=self.today + timedelta(days=1),
        )

        response = self._creer(
            date_distribution=self._datetime_local(self.today, time(10, 0)),
            provende=future_feed,
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("id_provende", response.data)
        self.assertFalse(ConsommationProvende.objects.exists())

    def test_basin_without_live_fish_is_rejected(self):
        empty_basin = Bassin.objects.create(
            reference="CONS-EMPTY",
            categorie=Bassin.Categorie.STOCKS,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )

        response = self.client.post(
            "/api/v1/provende/consommations-provende/",
            {
                "id_bassin": empty_basin.pk,
                "id_provende": self.provende.pk,
                "quantite_kg": "0.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("id_bassin", response.data)

    def test_zero_quantity_is_allowed_when_fish_are_present(self):
        response = self._creer(quantite_kg="0.00")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["quantite_kg"], "0.00")

    def test_cancellation_requires_nonempty_motif(self):
        distribution = self._creer()
        self.assertEqual(distribution.status_code, 201, distribution.data)

        response = self.client.post(
            (
                "/api/v1/provende/consommations-provende/"
                f"{distribution.data['id']}/annuler/"
            ),
            {"motif": "   "},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        row = ConsommationProvende.objects.get(pk=distribution.data["id"])
        self.assertEqual(row.statut, ConsommationProvende.Statut.VALIDE)

    def test_daily_totals_include_type_breakdown_and_only_valid_lines(self):
        first_time = self._datetime_local(self.today, time(9, 10))
        second_time = self._datetime_local(self.today, time(15, 10))
        first = enregistrer_distribution(
            {
                "bassin": self.bassin,
                "provende": self.provende,
                "date_distribution": first_time,
                "quantite_kg": Decimal("1.25"),
            },
            self.user,
        )
        enregistrer_distribution(
            {
                "bassin": self.bassin,
                "provende": self.provende_demarrage,
                "date_distribution": second_time,
                "quantite_kg": Decimal("2.75"),
            },
            self.user,
        )
        annuler_distribution(
            first.pk,
            motif="Exclure du total journalier",
            utilisateur=self.user,
        )

        totals = total_journalier(
            self.bassin.pk,
            self.today,
            self.today,
        )

        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]["total_kg"], Decimal("2.75"))
        self.assertEqual(totals[0]["nombre_distributions"], 1)
        self.assertEqual(totals[0]["detail_par_type"][0]["type_provende"], "Démarrage")

        response = self.client.get(
            "/api/v1/provende/consommations-provende/totaux-journaliers/",
            {
                "bassin": self.bassin.pk,
                "date_min": self.today.isoformat(),
                "date_max": self.today.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data[0]["nombre_distributions"], 1)

    def test_missing_days_use_effectif_cumul_without_daily_queries(self):
        date_min = self.today - timedelta(days=2)
        date_max = self.today
        history_basin = Bassin.objects.create(
            reference="CONS-HISTORY",
            categorie=Bassin.Categorie.STOCKS,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )
        self._ajouter_effectif(history_basin, date_min, 20)
        enregistrer_distribution(
            {
                "bassin": history_basin,
                "provende": self._create_provende(
                    self.type_provende,
                    "CONS-PROV-HIST",
                    date_creation=date_min,
                ),
                "date_distribution": self._datetime_local(date_min, time(8, 0)),
                "quantite_kg": Decimal("0.00"),
            },
            self.user,
        )

        missing = jours_manquants(history_basin.pk, date_min, date_max)

        self.assertEqual(
            missing,
            [date_min + timedelta(days=1), date_max],
        )
        response = self.client.get(
            "/api/v1/provende/consommations-provende/jours-manquants/",
            {
                "bassin": history_basin.pk,
                "date_min": date_min.isoformat(),
                "date_max": date_max.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data,
            [
                (date_min + timedelta(days=1)).isoformat(),
                date_max.isoformat(),
            ],
        )

    def test_missing_days_rejects_period_over_366_days(self):
        with self.assertRaises(ValidationError):
            jours_manquants(
                self.bassin.pk,
                self.today - timedelta(days=366),
                self.today,
            )

    def test_get_list_filters_by_basin_feed_type_and_local_dates(self):
        response = self._creer(
            date_distribution=self._datetime_local(self.today, time(10, 0)),
        )
        self.assertEqual(response.status_code, 201, response.data)

        filtered = self.client.get(
            "/api/v1/provende/consommations-provende/",
            {
                "bassin": self.bassin.pk,
                "provende": self.provende.pk,
                "type_provende": "Croissance",
                "date_min": self.today.isoformat(),
                "date_max": self.today.isoformat(),
                "statut": "VALIDE",
            },
        )

        self.assertEqual(filtered.status_code, 200, filtered.data)
        self.assertEqual(len(filtered.data), 1)
        self.assertEqual(filtered.data[0]["id"], response.data["id"])
