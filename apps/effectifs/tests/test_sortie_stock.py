from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bassins.models import Bassin, Hapa
from apps.ecloserie.models import Eclosion
from rest_framework.exceptions import ValidationError

from apps.effectifs.models import MouvementEffectif, SortieStock
from apps.effectifs.services import (
    effectif_vivant,
    enregistrer_mouvement,
    enregistrer_sortie_stock_validee,
    preparer_sortie_stock,
)
from apps.lots.models import Lot
from apps.reproduction.models import Ponte, Reproduction
from apps.stocks.models import MiseEnStock


User = get_user_model()


class SortieStockTests(TestCase):
    """Sortie totale du registre ; la création du lot de grossissement est testée
    dans apps.grossissement (flux en une seule transaction, sans brouillon)."""

    def setUp(self):
        role = User._meta.get_field("role").remote_field.model.objects.create(
            libelle="Administrateur",
        )
        self.user = User.objects.create_user(
            username="sortie-admin",
            password="test-password",
            role=role,
            statut="ACTIF",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.today = timezone.localdate()
        self.bassin_stock = self._bassin("SORTIE-STOCK", Bassin.Categorie.STOCKS)
        self.bassin_destination = self._bassin(
            "SORTIE-GROSS",
            Bassin.Categorie.GROSSISSEMENT,
        )
        self.bassin_reproduction = self._bassin(
            "SORTIE-REPRO",
            Bassin.Categorie.REPRODUCTION,
        )
        self.hapa_a = self._hapa(self.bassin_stock, "A")
        self.hapa_b = self._hapa(self.bassin_stock, "B")

    def _bassin(self, reference, categorie):
        return Bassin.objects.create(
            reference=reference,
            categorie=categorie,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )

    def _hapa(self, bassin, nom):
        return Hapa.objects.create(
            bassin=bassin,
            nom=nom,
            actif=True,
            statut=Hapa.Statut.ACTIF,
        )

    def _lot(self, reference):
        bassin_reproduction = self._bassin(
            f"REP-BASSIN-{reference}",
            Bassin.Categorie.REPRODUCTION,
        )
        reproduction = Reproduction.objects.create(
            reference=f"REP-{reference}",
            bassin=bassin_reproduction,
            espece=Lot.Espece.CARPE,
            date_ajout=self.today,
            quantite_male=1,
            quantite_femelle=1,
        )
        ponte = Ponte.objects.create(
            reference=f"PONTE-{reference}",
            reproduction=reproduction,
            date_ponte=self.today,
            quantite_oeuf_obtenu=20,
        )
        eclosion = Eclosion.objects.create(
            reference=f"ECLOSION-{reference}",
            ponte=ponte,
            date_eclosion=self.today,
            quantite_alevins_obtenu=20,
        )
        return Lot.objects.create(
            reference=reference,
            eclosion=eclosion,
            date_creation=timezone.now(),
            espece=Lot.Espece.CARPE,
            utilisateur_createur=self.user,
            quantite_initiale=20,
            parcours_production=Lot.ParcoursProduction.SANS_TRAITEMENT,
            stade=Lot.Stade.EN_STOCK,
        )

    def _mettre_en_stock(self, reference, effectifs, finalite="GROSSISSEMENT"):
        lot = self._lot(reference)
        MiseEnStock.objects.create(
            lot=lot,
            bassin=self.bassin_stock,
            finalite=finalite,
            effectif_entrant=sum(effectifs.values()),
            date_mise_en_stock=timezone.now(),
            utilisateur=self.user,
        )
        for hapa, quantite in effectifs.items():
            enregistrer_mouvement(
                bassin=self.bassin_stock,
                espece=lot.espece,
                type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
                quantite=quantite,
                date_evenement=self.today,
                saisi_par=self.user,
                lot_origine=lot,
                hapa=hapa,
            )
        return lot

    def _preparer(self, lot, *, destination=None, date_locale=None):
        return preparer_sortie_stock(
            lot,
            destination or self.bassin_destination,
            date_locale or self.today,
        )

    def _sortie_validee(self, lot, *, effectif_entrant=None, justification=""):
        with transaction.atomic():
            preparation = self._preparer(lot)
            return enregistrer_sortie_stock_validee(
                lot=lot,
                preparation=preparation,
                date_sortie=timezone.now(),
                effectif_entrant=(
                    preparation["effectif_sortant"]
                    if effectif_entrant is None
                    else effectif_entrant
                ),
                justification_ecart=justification,
                utilisateur=self.user,
            )

    def _mouvements_operation(self, sortie):
        return MouvementEffectif.objects.filter(
            operation_source_content_type=ContentType.objects.get_for_model(
                SortieStock
            ),
            operation_source_object_id=sortie.pk,
        )

    def _assert_refus(self, champ, fonction, *args, **kwargs):
        with self.assertRaises(ValidationError) as ctx:
            with transaction.atomic():
                fonction(*args, **kwargs)
        self.assertIn(champ, ctx.exception.detail)
        return ctx.exception.detail

    def test_validation_nominale_transfere_le_lot_entier(self):
        lot = self._mettre_en_stock("LOT-NOMINAL", {self.hapa_a: 10})

        sortie = self._sortie_validee(lot)

        self.assertEqual(sortie.statut, SortieStock.Statut.VALIDEE)
        self.assertEqual(sortie.validee_par, self.user)
        self.assertEqual(sortie.ecart_effectif, 0)
        movements = self._mouvements_operation(sortie)
        source = movements.get(type_mouvement="SORTIE")
        destination = movements.get(type_mouvement="ENTREE")
        self.assertEqual(source.hapa_id, self.hapa_a.pk)
        self.assertEqual(source.lot_origine_id, lot.pk)
        self.assertIsNone(destination.hapa_id)
        self.assertEqual(destination.lot_origine_id, lot.pk)
        self.assertEqual(effectif_vivant(self.bassin_stock.pk, lot.espece, self.today, self.hapa_a.pk), 0)
        self.assertEqual(effectif_vivant(self.bassin_destination.pk, lot.espece, self.today), 10)

    def test_lot_sur_deux_hapas_cree_une_sortie_par_hapa(self):
        lot = self._mettre_en_stock(
            "LOT-DEUX-HAPAS",
            {self.hapa_a: 7, self.hapa_b: 5},
        )

        sortie = self._sortie_validee(lot)

        sorties = self._mouvements_operation(sortie).filter(type_mouvement="SORTIE")
        self.assertEqual(
            set(sorties.values_list("hapa_id", "quantite")),
            {(self.hapa_a.pk, 7), (self.hapa_b.pk, 5)},
        )
        self.assertEqual(sortie.effectif_sortant, 12)

    def test_ecart_partiel_obligatoire_et_plafonne(self):
        lot = self._mettre_en_stock("LOT-ECART", {self.hapa_a: 10})
        initial_count = MouvementEffectif.objects.count()

        self._assert_refus(
            "justification_ecart",
            self._sortie_validee,
            lot,
            effectif_entrant=7,
        )
        # Écart en plus refusé, même justifié.
        self._assert_refus(
            "effectif_entrant",
            self._sortie_validee,
            lot,
            effectif_entrant=11,
            justification="Recomptage",
        )
        self.assertEqual(MouvementEffectif.objects.count(), initial_count)
        self.assertFalse(SortieStock.objects.exists())

        sortie = self._sortie_validee(
            lot,
            effectif_entrant=7,
            justification="Écart constaté au comptage",
        )
        self.assertEqual(sortie.ecart_effectif, -3)
        self.assertEqual(
            self._mouvements_operation(sortie).get(type_mouvement="ENTREE").quantite,
            7,
        )

    def test_finalite_vente_ou_absente_est_refusee(self):
        for suffix, finalite in (("VENTE", "VENTE"), ("SANS", None)):
            with self.subTest(finalite=finalite):
                lot = self._mettre_en_stock(
                    f"LOT-FINALITE-{suffix}",
                    {self.hapa_a: 5},
                    finalite=finalite,
                )
                self._assert_refus("id_lot", self._preparer, lot)

    def test_hapa_partage_refuse_sans_mouvement(self):
        lot = self._mettre_en_stock("LOT-PARTAGE-A", {self.hapa_a: 6})
        self._mettre_en_stock("LOT-PARTAGE-B", {self.hapa_a: 4})

        detail = self._assert_refus("id_lot", self._preparer, lot)

        self.assertIn("partage le hapa", str(detail["id_lot"]))
        self.assertFalse(
            MouvementEffectif.objects.filter(
                operation_source_object_id__isnull=False,
            ).exists()
        )

    def test_destination_invalide_et_lot_absent_sont_refuses(self):
        lot = self._mettre_en_stock("LOT-DESTINATION", {self.hapa_a: 5})
        self._assert_refus(
            "id_bassin_destination",
            self._preparer,
            lot,
            destination=self.bassin_reproduction,
        )

        absent = self._lot("LOT-ABSENT")
        self._assert_refus("id_lot", self._preparer, absent)
        self.assertEqual(MouvementEffectif.objects.count(), 1)

    def test_sortie_active_unique(self):
        lot = self._mettre_en_stock("LOT-UNIQUE", {self.hapa_a: 5})
        self._sortie_validee(lot)
        count_after_validation = MouvementEffectif.objects.count()

        self._assert_refus("id_lot", self._sortie_validee, lot)

        self.assertEqual(MouvementEffectif.objects.count(), count_after_validation)
        self.assertEqual(SortieStock.objects.filter(lot=lot).count(), 1)

    def test_ancien_flux_brouillon_et_validation_supprime(self):
        lot = self._mettre_en_stock("LOT-ANCIEN-FLUX", {self.hapa_a: 5})
        sortie = self._sortie_validee(lot)
        base = "/api/v1/effectifs/sorties-stock/"

        creation = self.client.post(
            base,
            {"id_lot": lot.pk, "id_bassin_destination": self.bassin_destination.pk},
            format="json",
        )
        modification = self.client.patch(
            f"{base}{sortie.pk}/",
            {"observation": "Modification"},
            format="json",
        )
        validation = self.client.post(
            f"{base}{sortie.pk}/valider/",
            {"poids_initial_g": "20.00"},
            format="json",
        )

        self.assertEqual(creation.status_code, 405, creation.data)
        self.assertEqual(modification.status_code, 405, modification.data)
        self.assertEqual(validation.status_code, 404)
        self.assertEqual(self.client.get(f"{base}{sortie.pk}/").status_code, 200)

    def test_annulation_restaure_les_effectifs_et_echoue_si_destination_vide(self):
        lot = self._mettre_en_stock("LOT-ANNULATION-A", {self.hapa_a: 5})
        sortie = self._sortie_validee(lot)
        sans_motif = self.client.post(
            f"/api/v1/effectifs/sorties-stock/{sortie.pk}/annuler/",
            {"motif": "   "},
            format="json",
        )
        self.assertEqual(sans_motif.status_code, 400, sans_motif.data)
        cancelled = self.client.post(
            f"/api/v1/effectifs/sorties-stock/{sortie.pk}/annuler/",
            {"motif": "Correction"},
            format="json",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(
            effectif_vivant(
                self.bassin_stock.pk,
                lot.espece,
                self.today,
                self.hapa_a.pk,
            ),
            5,
        )
        self.assertEqual(
            effectif_vivant(self.bassin_destination.pk, lot.espece, self.today),
            0,
        )

        second_lot = self._mettre_en_stock(
            "LOT-ANNULATION-B",
            {self.hapa_b: 5},
        )
        second = self._sortie_validee(second_lot)
        enregistrer_mouvement(
            bassin=self.bassin_destination,
            espece=second_lot.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
            quantite=5,
            date_evenement=self.today,
            saisi_par=self.user,
        )
        failed = self.client.post(
            f"/api/v1/effectifs/sorties-stock/{second.pk}/annuler/",
            {"motif": "Destination vidée"},
            format="json",
        )
        self.assertEqual(failed.status_code, 400, failed.data)
        self.assertEqual(
            SortieStock.objects.get(pk=second.pk).statut,
            SortieStock.Statut.VALIDEE,
        )

    def test_date_future_refusee(self):
        lot = self._mettre_en_stock("LOT-DATE", {self.hapa_a: 5})

        with self.assertRaises(ValidationError) as ctx:
            with transaction.atomic():
                preparation = self._preparer(lot)
                enregistrer_sortie_stock_validee(
                    lot=lot,
                    preparation=preparation,
                    date_sortie=timezone.now() + timedelta(days=1),
                    effectif_entrant=5,
                    justification_ecart="",
                    utilisateur=self.user,
                )

        self.assertIn("date_sortie", ctx.exception.detail)
        self.assertFalse(SortieStock.objects.exists())

    def test_prelevement_tient_compte_de_la_mortalite_precedente(self):
        lot = self._mettre_en_stock("LOT-MORTALITE", {self.hapa_a: 10})
        enregistrer_mouvement(
            bassin=self.bassin_stock,
            espece=lot.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.MORTALITE,
            quantite=3,
            date_evenement=self.today,
            saisi_par=self.user,
            lot_origine=lot,
            hapa=self.hapa_a,
        )
        with transaction.atomic():
            self.assertEqual(self._preparer(lot)["effectif_sortant"], 7)

        sortie = self._sortie_validee(lot)

        mouvement = self._mouvements_operation(sortie).get(type_mouvement="SORTIE")
        self.assertEqual(mouvement.quantite, 7)
