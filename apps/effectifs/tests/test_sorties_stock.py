from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bassins.models import Bassin

from apps.effectifs.models import MouvementEffectif, SortieStock
from apps.effectifs.services import solde_bassin_espece_date

User = get_user_model()


class SortieStockTests(TestCase):
    def setUp(self):
        role_model = User._meta.get_field("role").remote_field.model
        role = role_model.objects.create(libelle="Administrateur")
        self.user = User.objects.create_user(
            username="sortie-admin",
            password="TestPassword123!",
            role=role,
            statut="ACTIF",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.bassin_source = Bassin.objects.create(
            reference="SORTIE-SOURCE",
            categorie=Bassin.Categorie.STOCKS,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )
        self.bassin_destination = Bassin.objects.create(
            reference="SORTIE-GROSS",
            categorie=Bassin.Categorie.GROSSISSEMENT,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )
        self.date = timezone.localdate()
        self._mouvement(
            self.bassin_source,
            MouvementEffectif.TypeMouvement.ENTREE,
            20,
        )

    def _mouvement(self, bassin, type_mouvement, quantite):
        return MouvementEffectif.objects.create(
            bassin=bassin,
            espece="CARPE",
            type_mouvement=type_mouvement,
            sens=(
                MouvementEffectif.Sens.POSITIF
                if type_mouvement == MouvementEffectif.TypeMouvement.ENTREE
                else MouvementEffectif.Sens.NEGATIF
            ),
            quantite=quantite,
            date_evenement=self.date,
            saisi_par=self.user,
        )

    def _creer_sortie(self, **overrides):
        data = {
            "id_bassin_source": self.bassin_source.pk,
            "id_bassin_destination": self.bassin_destination.pk,
            "type_sortie": SortieStock.TypeSortie.GROSSISSEMENT,
            "espece": "CARPE",
            "quantite": 5,
            "date_sortie": timezone.now().isoformat(),
        }
        data.update(overrides)
        return self.client.post(
            "/api/v1/effectifs/sorties-stock/",
            data,
            format="json",
        )

    def _valider(self, sortie_id):
        return self.client.post(
            f"/api/v1/effectifs/sorties-stock/{sortie_id}/valider/",
            {},
            format="json",
        )

    def _solde(self, bassin):
        return solde_bassin_espece_date(
            bassin.pk,
            "CARPE",
            self.date,
        )

    def test_grossissement_validation_moves_fish_between_basins(self):
        response = self._creer_sortie()
        self.assertEqual(response.status_code, 201, response.data)
        sortie_id = response.data["id"]
        self.assertEqual(response.data["statut"], SortieStock.Statut.BROUILLON)
        self.assertEqual(self._solde(self.bassin_source), 20)
        self.assertEqual(self._solde(self.bassin_destination), 0)

        validated = self._valider(sortie_id)

        self.assertEqual(validated.status_code, 200, validated.data)
        self.assertEqual(validated.data["statut"], SortieStock.Statut.VALIDEE)
        self.assertEqual(self._solde(self.bassin_source), 15)
        self.assertEqual(self._solde(self.bassin_destination), 5)
        mouvements = MouvementEffectif.objects.exclude(
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            bassin=self.bassin_source,
        )
        self.assertEqual(mouvements.count(), 2)
        self.assertEqual(
            set(mouvements.values_list("type_mouvement", flat=True)),
            {"SORTIE", "ENTREE"},
        )

    def test_sale_validation_creates_vente_movement_only(self):
        response = self._creer_sortie(
            type_sortie=SortieStock.TypeSortie.VENTE,
            id_bassin_destination=None,
            quantite=4,
        )
        self.assertEqual(response.status_code, 201, response.data)

        validated = self._valider(response.data["id"])

        self.assertEqual(validated.status_code, 200, validated.data)
        self.assertEqual(self._solde(self.bassin_source), 16)
        self.assertEqual(
            MouvementEffectif.objects.filter(
                bassin=self.bassin_source,
                type_mouvement=MouvementEffectif.TypeMouvement.VENTE,
            ).count(),
            1,
        )
        self.assertEqual(
            MouvementEffectif.objects.filter(
                bassin=self.bassin_destination,
            ).count(),
            0,
        )

    def test_quantity_above_live_effectif_leaves_draft_and_no_movements(self):
        response = self._creer_sortie(quantite=21)
        self.assertEqual(response.status_code, 201, response.data)
        movement_count = MouvementEffectif.objects.count()

        validation = self._valider(response.data["id"])

        self.assertEqual(validation.status_code, 400, validation.data)
        self.assertIn("quantite", validation.data)
        sortie = SortieStock.objects.get(pk=response.data["id"])
        self.assertEqual(sortie.statut, SortieStock.Statut.BROUILLON)
        self.assertEqual(MouvementEffectif.objects.count(), movement_count)

    def test_wrong_source_category_and_inactive_destination_are_rejected(self):
        wrong_source = Bassin.objects.create(
            reference="SORTIE-NON-STOCK",
            categorie=Bassin.Categorie.REPRODUCTION,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )
        wrong_source_response = self._creer_sortie(
            id_bassin_source=wrong_source.pk,
        )
        self.assertEqual(wrong_source_response.status_code, 400)
        self.assertIn("id_bassin_source", wrong_source_response.data)

        self.bassin_destination.statut = Bassin.Statut.INACTIF
        self.bassin_destination.save(update_fields=["statut"])
        inactive_destination = self._creer_sortie()
        self.assertEqual(inactive_destination.status_code, 400)
        self.assertIn("id_bassin_destination", inactive_destination.data)

    def test_double_validation_is_rejected(self):
        response = self._creer_sortie()
        first = self._valider(response.data["id"])
        second = self._valider(response.data["id"])

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 400, second.data)
        self.assertEqual(MouvementEffectif.objects.count(), 3)

    def test_cancelling_validated_transfer_restores_both_balances(self):
        response = self._creer_sortie()
        self.assertEqual(self._valider(response.data["id"]).status_code, 200)
        self.assertEqual(self._solde(self.bassin_source), 15)
        self.assertEqual(self._solde(self.bassin_destination), 5)

        cancelled = self.client.post(
            f"/api/v1/effectifs/sorties-stock/{response.data['id']}/annuler/",
            {"motif": "Correction du transfert"},
            format="json",
        )

        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(cancelled.data["statut"], SortieStock.Statut.ANNULEE)
        self.assertEqual(self._solde(self.bassin_source), 20)
        self.assertEqual(self._solde(self.bassin_destination), 0)

    def test_cancellation_rolls_back_if_destination_no_longer_has_fish(self):
        response = self._creer_sortie()
        self.assertEqual(self._valider(response.data["id"]).status_code, 200)
        self._mouvement(
            self.bassin_destination,
            MouvementEffectif.TypeMouvement.SORTIE,
            5,
        )
        movement_count = MouvementEffectif.objects.count()

        cancelled = self.client.post(
            f"/api/v1/effectifs/sorties-stock/{response.data['id']}/annuler/",
            {"motif": "Annulation impossible"},
            format="json",
        )

        self.assertEqual(cancelled.status_code, 400, cancelled.data)
        sortie = SortieStock.objects.get(pk=response.data["id"])
        self.assertEqual(sortie.statut, SortieStock.Statut.VALIDEE)
        self.assertEqual(MouvementEffectif.objects.count(), movement_count)

    def test_cancellation_without_motif_is_rejected(self):
        response = self._creer_sortie()
        self.assertEqual(self._valider(response.data["id"]).status_code, 200)

        cancelled = self.client.post(
            f"/api/v1/effectifs/sorties-stock/{response.data['id']}/annuler/",
            {"motif": "   "},
            format="json",
        )

        self.assertEqual(cancelled.status_code, 400, cancelled.data)
        self.assertEqual(
            SortieStock.objects.get(pk=response.data["id"]).statut,
            SortieStock.Statut.VALIDEE,
        )

    def test_validated_stock_exit_cannot_be_patched(self):
        response = self._creer_sortie()
        self.assertEqual(self._valider(response.data["id"]).status_code, 200)

        patched = self.client.patch(
            f"/api/v1/effectifs/sorties-stock/{response.data['id']}/",
            {"quantite": 3},
            format="json",
        )

        self.assertEqual(patched.status_code, 400, patched.data)
        self.assertEqual(
            SortieStock.objects.get(pk=response.data["id"]).quantite,
            5,
        )

    def test_grossissement_without_destination_is_rejected(self):
        response = self._creer_sortie(id_bassin_destination=None)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("id_bassin_destination", response.data)
        self.assertFalse(SortieStock.objects.exists())
