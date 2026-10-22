from datetime import date, timedelta
from io import StringIO
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.bassins.models import Bassin
from apps.core.models import Role
from apps.ecloserie.models import Eclosion
from apps.lots.models import Lot
from apps.provende.models import Provende, TypeProvende
from apps.reproduction.models import Ponte, Reproduction
from apps.stocks.models import MiseEnStock
from apps.traitement.models import Traitement

from .models import (
    CategoriePerte,
    CauseMortalite,
    EvenementMortalitePerte,
    MouvementEffectif,
)
from .services import (
    annuler_mouvement,
    enregistrer_mouvement,
    enregistrer_evenement_mortalite_perte,
    solde_bassin_espece_date,
)

User = get_user_model()


class EffectifServiceTests(TestCase):
    def setUp(self):
        self.role = self._create_role("Administrateur")
        self.user = User.objects.create_user(
            username="admin",
            password="Password123!",
            role=self.role,
            statut="ACTIF",
        )
        self.bassin = Bassin.objects.create(
            reference="B-001",
            categorie=Bassin.Categorie.REPRODUCTION,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )

    def _create_role(self, libelle):
        return Role.objects.create(libelle=libelle, description="Test")

    def test_refuse_negative_balance(self):
        enregistrer_mouvement(
            bassin=self.bassin,
            espece="TILAPIA",
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            quantite=10,
            date_evenement=timezone.localdate(),
            saisi_par=self.user,
        )

        with self.assertRaises(ValidationError):
            enregistrer_mouvement(
                bassin=self.bassin,
                espece="TILAPIA",
                type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
                quantite=11,
                date_evenement=timezone.localdate(),
                saisi_par=self.user,
            )

    def test_retroactive_insert_requires_non_negative_history(self):
        enregistrer_mouvement(
            bassin=self.bassin,
            espece="TILAPIA",
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            quantite=5,
            date_evenement=date(2024, 1, 10),
            saisi_par=self.user,
        )

        with self.assertRaises(ValidationError):
            enregistrer_mouvement(
                bassin=self.bassin,
                espece="TILAPIA",
                type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
                quantite=7,
                date_evenement=date(2024, 1, 1),
                saisi_par=self.user,
            )

    def test_cancel_mouvement_creates_counter_movement(self):
        entree = enregistrer_mouvement(
            bassin=self.bassin,
            espece="TILAPIA",
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            quantite=8,
            date_evenement=date(2024, 2, 1),
            saisi_par=self.user,
        )

        annulation = annuler_mouvement(
            entree.pk,
            motif_annulation="Erreur de saisie",
            saisi_par=self.user,
            date_annulation=date(2024, 2, 1),
        )

        self.assertEqual(annulation.type_mouvement, MouvementEffectif.TypeMouvement.ANNULATION)
        self.assertEqual(annulation.sens, -entree.sens)
        self.assertEqual(annulation.quantite, entree.quantite)
        self.assertEqual(annulation.annule_mouvement_id, entree.id)

    def test_double_cancel_is_rejected(self):
        entree = enregistrer_mouvement(
            bassin=self.bassin,
            espece="TILAPIA",
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            quantite=4,
            date_evenement=date(2024, 3, 1),
            saisi_par=self.user,
        )

        annuler_mouvement(
            entree.pk,
            motif_annulation="Première annulation",
            saisi_par=self.user,
            date_annulation=date(2024, 3, 1),
        )

        with self.assertRaises(ValidationError):
            annuler_mouvement(
                entree.pk,
                motif_annulation="Seconde annulation",
                saisi_par=self.user,
                date_annulation=date(2024, 3, 1),
            )


class EvenementMortalitePerteTests(TestCase):
    def setUp(self):
        role, _ = Role.objects.get_or_create(
            libelle="Administrateur",
            defaults={"description": "Tests du journal des pertes"},
        )
        self.user = User.objects.create_user(
            username="mortality-admin",
            password="TestPassword123!",
            role=role,
            statut="ACTIF",
        )
        self.bassin = Bassin.objects.create(
            reference="MORT-001",
            categorie=Bassin.Categorie.STOCKS,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.today = timezone.localdate()
        self.cause = CauseMortalite.objects.get(libelle="Inconnue")
        self.categorie = CategoriePerte.objects.get(libelle="Autre")

    def _add_stock(self, quantity=12):
        return enregistrer_mouvement(
            bassin=self.bassin,
            espece="CARPE",
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            quantite=quantity,
            date_evenement=self.today,
            saisi_par=self.user,
        )

    def test_mortality_and_loss_create_linked_debit_movements(self):
        self._add_stock()

        mortality = enregistrer_evenement_mortalite_perte(
            bassin=self.bassin,
            espece="CARPE",
            type_evenement=EvenementMortalitePerte.TypeEvenement.MORTALITE,
            quantite=2,
            date_evenement=self.today,
            saisi_par=self.user,
            cause_mortalite=self.cause,
        )
        loss = enregistrer_evenement_mortalite_perte(
            bassin=self.bassin,
            espece="CARPE",
            type_evenement=EvenementMortalitePerte.TypeEvenement.PERTE,
            quantite=3,
            date_evenement=self.today,
            saisi_par=self.user,
            categorie_perte=self.categorie,
            justification="Comptage physique inférieur au registre",
        )

        self.assertEqual(mortality.mouvement.type_mouvement, "MORTALITE")
        self.assertEqual(loss.mouvement.type_mouvement, "PERTE")
        self.assertEqual(
            solde_bassin_espece_date(self.bassin.pk, "CARPE", self.today),
            7,
        )

    def test_loss_without_justification_is_rejected_without_partial_records(self):
        self._add_stock()

        response = self.client.post(
            "/api/v1/effectifs/evenements/",
            {
                "bassin": self.bassin.pk,
                "espece": "CARPE",
                "type_evenement": "PERTE",
                "quantite": 2,
                "date_evenement": self.today.isoformat(),
                "categorie_perte": self.categorie.pk,
                "justification": "   ",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("justification", response.data)
        self.assertFalse(EvenementMortalitePerte.objects.exists())
        self.assertEqual(MouvementEffectif.objects.count(), 1)

    def test_database_constraints_reject_invalid_event_reference_combinations(self):
        invalid_events = (
            {
                "type_evenement": EvenementMortalitePerte.TypeEvenement.MORTALITE,
                "cause_mortalite": None,
                "categorie_perte": None,
                "justification": "",
            },
            {
                "type_evenement": EvenementMortalitePerte.TypeEvenement.PERTE,
                "cause_mortalite": None,
                "categorie_perte": self.categorie,
                "justification": "",
            },
        )
        for event_fields in invalid_events:
            with self.subTest(event_type=event_fields["type_evenement"]):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        EvenementMortalitePerte.objects.create(
                            bassin=self.bassin,
                            espece="CARPE",
                            quantite=1,
                            date_evenement=self.today,
                            saisi_par=self.user,
                            **event_fields,
                        )

    def test_mortality_requires_cause_and_rejects_excess_quantity(self):
        self._add_stock(quantity=5)
        missing_cause = self.client.post(
            "/api/v1/effectifs/evenements/",
            {
                "bassin": self.bassin.pk,
                "espece": "CARPE",
                "type_evenement": "MORTALITE",
                "quantite": 1,
                "date_evenement": self.today.isoformat(),
            },
            format="json",
        )
        self.assertEqual(missing_cause.status_code, 400, missing_cause.data)

        excessive = self.client.post(
            "/api/v1/effectifs/evenements/",
            {
                "bassin": self.bassin.pk,
                "espece": "CARPE",
                "type_evenement": "MORTALITE",
                "quantite": 6,
                "date_evenement": self.today.isoformat(),
                "cause_mortalite": self.cause.pk,
            },
            format="json",
        )
        self.assertEqual(excessive.status_code, 400, excessive.data)
        self.assertFalse(EvenementMortalitePerte.objects.exists())
        self.assertEqual(MouvementEffectif.objects.count(), 1)

    def test_journal_accepts_each_basin_category(self):
        for category in Bassin.Categorie:
            with self.subTest(category=category):
                basin = Bassin.objects.create(
                    reference=f"MORT-{category.value}",
                    categorie=category,
                    capacity=100,
                    statut=Bassin.Statut.ACTIF,
                )
                enregistrer_mouvement(
                    bassin=basin,
                    espece="CARPE",
                    type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
                    quantite=2,
                    date_evenement=self.today,
                    saisi_par=self.user,
                )

                event = enregistrer_evenement_mortalite_perte(
                    bassin=basin,
                    espece="CARPE",
                    type_evenement=EvenementMortalitePerte.TypeEvenement.MORTALITE,
                    quantite=1,
                    date_evenement=self.today,
                    saisi_par=self.user,
                    cause_mortalite=self.cause,
                )

                self.assertEqual(event.bassin.categorie, category)

    def test_inactive_references_basin_and_future_date_are_rejected(self):
        self._add_stock()
        payload = {
            "bassin": self.bassin.pk,
            "espece": "CARPE",
            "type_evenement": "MORTALITE",
            "quantite": 1,
            "date_evenement": self.today.isoformat(),
            "cause_mortalite": self.cause.pk,
        }

        self.cause.actif = False
        self.cause.save(update_fields=["actif"])
        inactive_cause = self.client.post(
            "/api/v1/effectifs/evenements/",
            payload,
            format="json",
        )
        self.assertEqual(inactive_cause.status_code, 400, inactive_cause.data)

        self.cause.actif = True
        self.cause.save(update_fields=["actif"])
        self.bassin.statut = Bassin.Statut.INACTIF
        self.bassin.save(update_fields=["statut"])
        inactive_basin = self.client.post(
            "/api/v1/effectifs/evenements/",
            payload,
            format="json",
        )
        self.assertEqual(inactive_basin.status_code, 400, inactive_basin.data)

        self.bassin.statut = Bassin.Statut.ACTIF
        self.bassin.save(update_fields=["statut"])
        payload["date_evenement"] = (self.today + timedelta(days=1)).isoformat()
        future_date = self.client.post(
            "/api/v1/effectifs/evenements/",
            payload,
            format="json",
        )
        self.assertEqual(future_date.status_code, 400, future_date.data)
        self.assertFalse(EvenementMortalitePerte.objects.exists())
        self.assertEqual(MouvementEffectif.objects.count(), 1)

    def test_cancellation_marks_event_and_compensates_movement(self):
        self._add_stock()
        event = enregistrer_evenement_mortalite_perte(
            bassin=self.bassin,
            espece="CARPE",
            type_evenement=EvenementMortalitePerte.TypeEvenement.MORTALITE,
            quantite=2,
            date_evenement=self.today,
            saisi_par=self.user,
            cause_mortalite=self.cause,
        )

        response = self.client.post(
            f"/api/v1/effectifs/evenements/{event.pk}/annuler/",
            {"motif_annulation": "Événement saisi par erreur"},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        event.refresh_from_db()
        self.assertEqual(event.statut, EvenementMortalitePerte.Statut.ANNULE)
        self.assertEqual(
            response.data["mouvement_annulation"]["annule_mouvement"],
            event.mouvement_id,
        )
        self.assertEqual(
            solde_bassin_espece_date(self.bassin.pk, "CARPE", self.today),
            12,
        )

        repeated = self.client.post(
            f"/api/v1/effectifs/evenements/{event.pk}/annuler/",
            {"motif_annulation": "Deuxième tentative"},
            format="json",
        )
        self.assertEqual(repeated.status_code, 400, repeated.data)
        self.assertEqual(MouvementEffectif.objects.count(), 3)

    def test_event_api_filters_by_basin_species_period_and_cause(self):
        self._add_stock()
        event = enregistrer_evenement_mortalite_perte(
            bassin=self.bassin,
            espece="CARPE",
            type_evenement=EvenementMortalitePerte.TypeEvenement.MORTALITE,
            quantite=1,
            date_evenement=self.today,
            saisi_par=self.user,
            cause_mortalite=self.cause,
        )

        response = self.client.get(
            "/api/v1/effectifs/evenements/",
            {
                "bassin": self.bassin.pk,
                "espece": "CARPE",
                "date_debut": self.today.isoformat(),
                "date_fin": self.today.isoformat(),
                "cause": self.cause.pk,
            },
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["id"] for row in response.data], [event.pk])


@skipUnlessDBFeature("has_select_for_update")
class EffectifConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        role = Role.objects.create(libelle="Administrateur")
        self.user = User.objects.create_user(
            username="concurrent-admin",
            password="Password123!",
            role=role,
            statut="ACTIF",
        )
        self.bassin = Bassin.objects.create(
            reference="B-CONCURRENT",
            categorie=Bassin.Categorie.TRAITEMENT,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )
        enregistrer_mouvement(
            bassin=self.bassin,
            espece="TILAPIA",
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            quantite=10,
            date_evenement=timezone.localdate(),
            saisi_par=self.user,
        )

    def test_concurrent_outputs_cannot_overdraw_basin(self):
        barrier = Barrier(2)

        def enregistrer_sortie():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                enregistrer_mouvement(
                    bassin=self.bassin,
                    espece="TILAPIA",
                    type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
                    quantite=7,
                    date_evenement=timezone.localdate(),
                    saisi_par=self.user,
                )
                return "created"
            except ValidationError:
                return "rejected"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(
                lambda _: enregistrer_sortie(),
                range(2),
            ))

        self.assertCountEqual(outcomes, ["created", "rejected"])
        self.assertEqual(
            solde_bassin_espece_date(
                self.bassin.pk,
                "TILAPIA",
                timezone.localdate(),
            ),
            3,
        )


class EffectifOperationIntegrationTests(TestCase):
    def setUp(self):
        role = Role.objects.create(
            libelle="Administrateur",
            description="Tests d’intégration",
        )
        self.user = User.objects.create_user(
            username="registry-admin",
            password="Password123!",
            role=role,
            statut="ACTIF",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.bassin_reproduction = Bassin.objects.create(
            reference="REP-API-01",
            categorie=Bassin.Categorie.REPRODUCTION,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )
        self.bassin_traitement = Bassin.objects.create(
            reference="TRAIT-API-01",
            categorie=Bassin.Categorie.TRAITEMENT,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )
        self.bassin_stock = Bassin.objects.create(
            reference="STOCK-API-01",
            categorie=Bassin.Categorie.STOCKS,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )
        self.provende_type, _ = TypeProvende.objects.get_or_create(
            type="TRAITEMENT"
        )
        self.provende = Provende.objects.create(
            type_provende=self.provende_type,
            reference="PROV-API-01",
            date_creation=timezone.localdate(),
        )

    def _create_lot(self, quantite=20):
        reproduction = Reproduction.objects.create(
            reference="REP-SOURCE-01",
            bassin=self.bassin_reproduction,
            espece="TILAPIA",
            date_ajout=timezone.localdate(),
            quantite_male=2,
            quantite_femelle=3,
        )
        ponte = Ponte.objects.create(
            reference="PONTE-SOURCE-01",
            reproduction=reproduction,
            date_ponte=timezone.localdate(),
            quantite_oeuf_obtenu=100,
        )
        eclosion = Eclosion.objects.create(
            reference="ECLOSION-SOURCE-01",
            ponte=ponte,
            date_eclosion=timezone.localdate(),
            quantite_oeufs_eclos=80,
            quantite_alevins_obtenu=quantite,
        )
        return Lot.objects.create(
            reference="LOT-API-01",
            eclosion=eclosion,
            date_creation=timezone.now(),
            espece="TILAPIA",
            utilisateur_createur=self.user,
            quantite_initiale=quantite,
        )

    def test_reproduction_creation_registers_entry_atomically(self):
        response = self.client.post(
            "/api/v1/reproduction/reproductions/",
            {
                "reference": "REP-API-CREATED",
                "bassin": self.bassin_reproduction.pk,
                "espece": "CARPE",
                "date_ajout": timezone.localdate().isoformat(),
                "quantite_male": 4,
                "quantite_femelle": 6,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        reproduction = Reproduction.objects.get(reference="REP-API-CREATED")
        movement = MouvementEffectif.objects.get(
            operation_source_content_type=ContentType.objects.get_for_model(
                Reproduction
            ),
            operation_source_object_id=reproduction.pk,
        )
        self.assertEqual(movement.type_mouvement, "ENTREE")
        self.assertEqual(movement.quantite, 10)
        self.assertEqual(movement.bassin, self.bassin_reproduction)

        patch_response = self.client.patch(
            f"/api/v1/reproduction/reproductions/{reproduction.pk}/",
            {"quantite_male": 40},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 400, patch_response.data)
        reproduction.refresh_from_db()
        self.assertEqual(reproduction.quantite_male, 4)

        registry_post = self.client.post(
            "/api/v1/effectifs/",
            {
                "bassin": self.bassin_reproduction.pk,
                "espece": "CARPE",
                "type_mouvement": "ENTREE",
                "quantite": 999,
                "date_evenement": timezone.localdate().isoformat(),
            },
            format="json",
        )
        self.assertEqual(registry_post.status_code, 405)

        registry_patch = self.client.patch(
            f"/api/v1/effectifs/{movement.pk}/",
            {"quantite": 999},
            format="json",
        )
        self.assertEqual(registry_patch.status_code, 405)

    def test_treatment_creation_and_stocking_register_a_transfer(self):
        lot = self._create_lot()
        treatment_response = self.client.post(
            "/api/v1/traitement/",
            {
                "id_lot": lot.pk,
                "effectif_initial": 20,
                "date_ouverture": timezone.localdate().isoformat(),
                "id_bassin": self.bassin_traitement.pk,
                "id_used_provende": self.provende.pk,
            },
            format="json",
        )

        self.assertEqual(treatment_response.status_code, 201, treatment_response.data)
        treatment = Traitement.objects.get(pk=treatment_response.data["id"])
        self.assertEqual(
            solde_bassin_espece_date(
                self.bassin_traitement.pk,
                "TILAPIA",
                timezone.localdate(),
            ),
            20,
        )

        rejected_close = self.client.post(
            f"/api/v1/traitement/{treatment.pk}/cloturer/",
            {"effectif_final": 19},
            format="json",
        )
        self.assertEqual(rejected_close.status_code, 400, rejected_close.data)

        close_response = self.client.post(
            f"/api/v1/traitement/{treatment.pk}/cloturer/",
            {"effectif_final": 20},
            format="json",
        )
        self.assertEqual(close_response.status_code, 200, close_response.data)

        stock_response = self.client.post(
            "/api/v1/stocks/",
            {
                "id_lot": lot.pk,
                "id_bassin": self.bassin_stock.pk,
                "effectif_entrant": 20,
                "justification_ecart": "",
                "observation": "Transfert de test",
            },
            format="json",
        )
        self.assertEqual(stock_response.status_code, 201, stock_response.data)

        patch_response = self.client.patch(
            f"/api/v1/stocks/{stock_response.data['id']}/",
            {"effectif_entrant": 18},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 400, patch_response.data)

        self.assertEqual(
            solde_bassin_espece_date(
                self.bassin_traitement.pk,
                "TILAPIA",
                timezone.localdate(),
            ),
            0,
        )
        self.assertEqual(
            solde_bassin_espece_date(
                self.bassin_stock.pk,
                "TILAPIA",
                timezone.localdate(),
            ),
            20,
        )
        balance_response = self.client.get(
            "/api/v1/effectifs/solde/",
            {
                "bassin": self.bassin_stock.pk,
                "espece": "TILAPIA",
                "date": timezone.localdate().isoformat(),
            },
        )
        self.assertEqual(balance_response.status_code, 200)
        self.assertEqual(balance_response.data["effectif_vivant"], 20)
        self.assertEqual(
            MouvementEffectif.objects.filter(
                operation_source_content_type=ContentType.objects.get_for_model(
                    MiseEnStock
                ),
                operation_source_object_id=stock_response.data["id"],
            ).count(),
            2,
        )

    def test_failed_treatment_movement_rolls_back_operation(self):
        lot = self._create_lot()
        future_date = timezone.localdate() + timedelta(days=1)

        response = self.client.post(
            "/api/v1/traitement/",
            {
                "id_lot": lot.pk,
                "effectif_initial": 20,
                "date_ouverture": future_date.isoformat(),
                "id_bassin": self.bassin_traitement.pk,
                "id_used_provende": self.provende.pk,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Traitement.objects.filter(lot=lot).exists())
        lot.refresh_from_db()
        self.assertEqual(lot.stade, Lot.Stade.CREEE)
        self.assertFalse(
            MouvementEffectif.objects.filter(lot_origine=lot).exists()
        )

    def test_backfill_is_idempotent_and_reconciliation_matches(self):
        lot = self._create_lot()
        Traitement.objects.create(
            lot=lot,
            effectif_initial=20,
            date_ouverture=timezone.localdate(),
            bassin=self.bassin_traitement,
            used_provende=self.provende,
            utilisateur=self.user,
        )
        call_command(
            "reprendre_effectifs",
            user_id=self.user.pk,
            stdout=StringIO(),
        )
        first_count = MouvementEffectif.objects.count()
        call_command(
            "reprendre_effectifs",
            user_id=self.user.pk,
            stdout=StringIO(),
        )
        self.assertEqual(MouvementEffectif.objects.count(), first_count)

        output = StringIO()
        call_command("reconcilier_effectifs", stdout=output)
        self.assertIn("Aucun écart détecté.", output.getvalue())

    def test_mise_en_stock_rejects_unlogged_treatment_loss(self):
        lot = self._create_lot()
        traitement = Traitement.objects.create(
            lot=lot,
            effectif_initial=20,
            effectif_final=20,
            date_ouverture=timezone.localdate(),
            date_cloture=timezone.localdate(),
            bassin=self.bassin_traitement,
            used_provende=self.provende,
            utilisateur=self.user,
            statut=Traitement.Statut.CLOTUREE,
        )
        lot.stade = Lot.Stade.EN_TRAITEMENT
        lot.save(update_fields=["stade"])

        response = self.client.post(
            "/api/v1/stocks/",
            {
                "id_lot": lot.pk,
                "id_bassin": self.bassin_stock.pk,
                "effectif_entrant": 18,
                "justification_ecart": "Écart test",
                "observation": "",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(MiseEnStock.objects.filter(lot=lot).exists())
        self.assertFalse(
            MouvementEffectif.objects.filter(lot_origine=lot).exists()
        )
