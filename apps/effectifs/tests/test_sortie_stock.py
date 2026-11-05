from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bassins.models import Bassin, Hapa
from apps.ecloserie.models import Eclosion
from apps.effectifs.models import MouvementEffectif, SortieStock
from apps.effectifs.services import enregistrer_mouvement, effectif_vivant
from apps.lots.models import Lot
from apps.reproduction.models import Ponte, Reproduction
from apps.stocks.models import MiseEnStock


User = get_user_model()


class SortieStockTests(TestCase):
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

    def _creer_sortie(self, lot, *, destination=None, date_sortie=None):
        data = {
            "id_lot": lot.pk,
            "id_bassin_destination": (
                destination or self.bassin_destination
            ).pk,
        }
        if date_sortie is not None:
            data["date_sortie"] = date_sortie.isoformat()
        return self.client.post(
            "/api/v1/effectifs/sorties-stock/",
            data,
            format="json",
        )

    def _valider(self, sortie_id, data=None):
        return self.client.post(
            f"/api/v1/effectifs/sorties-stock/{sortie_id}/valider/",
            data or {},
            format="json",
        )

    def _mouvements_operation(self, sortie):
        return MouvementEffectif.objects.filter(
            operation_source_content_type=ContentType.objects.get_for_model(
                SortieStock
            ),
            operation_source_object_id=sortie.pk,
        )

    def test_validation_nominale_transfere_le_lot_entier(self):
        lot = self._mettre_en_stock("LOT-NOMINAL", {self.hapa_a: 10})
        draft = self._creer_sortie(lot)
        self.assertEqual(draft.status_code, 201, draft.data)

        response = self._valider(draft.data["id"])

        self.assertEqual(response.status_code, 200, response.data)
        sortie = SortieStock.objects.get(pk=draft.data["id"])
        self.assertEqual(sortie.statut, SortieStock.Statut.VALIDEE)
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
        draft = self._creer_sortie(lot)
        response = self._valider(draft.data["id"])

        self.assertEqual(response.status_code, 200, response.data)
        sorties = self._mouvements_operation(
            SortieStock.objects.get(pk=draft.data["id"])
        ).filter(type_mouvement="SORTIE")
        self.assertEqual(
            set(sorties.values_list("hapa_id", "quantite")),
            {(self.hapa_a.pk, 7), (self.hapa_b.pk, 5)},
        )
        self.assertEqual(response.data["effectif_sortant"], 12)

    def test_ecart_partiel_obligatoire_et_plafonne(self):
        lot = self._mettre_en_stock("LOT-ECART", {self.hapa_a: 10})
        draft = self._creer_sortie(lot)
        sortie_id = draft.data["id"]
        initial_count = MouvementEffectif.objects.count()

        sans_justification = self._valider(
            sortie_id,
            {"effectif_entrant": 7},
        )
        au_dessus = self._valider(
            sortie_id,
            {"effectif_entrant": 11},
        )
        accepte = self._valider(
            sortie_id,
            {
                "effectif_entrant": 7,
                "justification_ecart": "Écart constaté au comptage",
            },
        )

        self.assertEqual(sans_justification.status_code, 400)
        self.assertEqual(au_dessus.status_code, 400)
        self.assertEqual(MouvementEffectif.objects.count(), initial_count + 2)
        self.assertEqual(accepte.status_code, 200, accepte.data)
        sortie = SortieStock.objects.get(pk=sortie_id)
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
                response = self._creer_sortie(lot)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("id_lot", response.data)

    def test_hapa_partage_refuse_sans_mouvement(self):
        lot = self._mettre_en_stock("LOT-PARTAGE-A", {self.hapa_a: 6})
        self._mettre_en_stock("LOT-PARTAGE-B", {self.hapa_a: 4})
        draft = self._creer_sortie(lot)
        self.assertEqual(draft.status_code, 400, draft.data)
        self.assertIn("partage le hapa", str(draft.data["id_lot"]))
        self.assertFalse(
            MouvementEffectif.objects.filter(
                operation_source_object_id__isnull=False,
            ).exists()
        )

    def test_destination_invalide_et_lot_absent_sont_refuses(self):
        lot = self._mettre_en_stock("LOT-DESTINATION", {self.hapa_a: 5})
        response = self._creer_sortie(lot, destination=self.bassin_reproduction)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("id_bassin_destination", response.data)

        absent = self._lot("LOT-ABSENT")
        absent_response = self._creer_sortie(absent)
        self.assertEqual(absent_response.status_code, 400, absent_response.data)
        self.assertIn("id_lot", absent_response.data)
        self.assertEqual(MouvementEffectif.objects.count(), 1)

    def test_sortie_active_unique_et_double_validation_refusees(self):
        lot = self._mettre_en_stock("LOT-UNIQUE", {self.hapa_a: 5})
        first = self._creer_sortie(lot)
        second = self._creer_sortie(lot)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 400, second.data)

        validated = self._valider(first.data["id"])
        count_after_validation = MouvementEffectif.objects.count()
        repeated = self._valider(first.data["id"])
        self.assertEqual(validated.status_code, 200, validated.data)
        self.assertEqual(repeated.status_code, 400, repeated.data)
        self.assertEqual(MouvementEffectif.objects.count(), count_after_validation)

    def test_annulation_restaure_les_effectifs_et_echoue_si_destination_vide(self):
        lot = self._mettre_en_stock("LOT-ANNULATION-A", {self.hapa_a: 5})
        draft = self._creer_sortie(lot)
        self.assertEqual(self._valider(draft.data["id"]).status_code, 200)
        sans_motif = self.client.post(
            f"/api/v1/effectifs/sorties-stock/{draft.data['id']}/annuler/",
            {"motif": "   "},
            format="json",
        )
        self.assertEqual(sans_motif.status_code, 400, sans_motif.data)
        cancelled = self.client.post(
            f"/api/v1/effectifs/sorties-stock/{draft.data['id']}/annuler/",
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
        second = self._creer_sortie(second_lot)
        self.assertEqual(self._valider(second.data["id"]).status_code, 200)
        enregistrer_mouvement(
            bassin=self.bassin_destination,
            espece=second_lot.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
            quantite=5,
            date_evenement=self.today,
            saisi_par=self.user,
        )
        failed = self.client.post(
            f"/api/v1/effectifs/sorties-stock/{second.data['id']}/annuler/",
            {"motif": "Destination vidée"},
            format="json",
        )
        self.assertEqual(failed.status_code, 400, failed.data)
        self.assertEqual(
            SortieStock.objects.get(pk=second.data["id"]).statut,
            SortieStock.Statut.VALIDEE,
        )

    def test_date_future_et_patch_apres_validation_sont_refuses(self):
        lot = self._mettre_en_stock("LOT-DATE", {self.hapa_a: 5})
        future = self._creer_sortie(
            lot,
            date_sortie=timezone.now() + timedelta(days=1),
        )
        self.assertEqual(future.status_code, 400, future.data)
        self.assertIn("date_sortie", future.data)

        draft = self._creer_sortie(lot)
        self.assertEqual(self._valider(draft.data["id"]).status_code, 200)
        patched = self.client.patch(
            f"/api/v1/effectifs/sorties-stock/{draft.data['id']}/",
            {"observation": "Modification"},
            format="json",
        )
        self.assertEqual(patched.status_code, 400, patched.data)

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
        draft = self._creer_sortie(lot)
        preview = self.client.get(
            f"/api/v1/effectifs/sorties-stock/{draft.data['id']}/apercu/"
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["effectif_sortant"], 7)
        response = self._valider(draft.data["id"])
        self.assertEqual(response.status_code, 200, response.data)
        mouvement = self._mouvements_operation(
            SortieStock.objects.get(pk=draft.data["id"])
        ).get(type_mouvement="SORTIE")
        self.assertEqual(mouvement.quantite, 7)
