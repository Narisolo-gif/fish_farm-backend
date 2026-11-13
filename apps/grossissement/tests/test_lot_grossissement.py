from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bassins.models import Bassin, Hapa
from apps.core.models import Role
from apps.ecloserie.models import Eclosion
from rest_framework.exceptions import ValidationError

from apps.effectifs.models import MouvementEffectif, SortieStock
from apps.effectifs.services import effectif_vivant, enregistrer_mouvement
from apps.grossissement.models import LotGrossissement
from apps.grossissement.services import (
    cloturer_lot_grossissement,
    verifier_capacite_bassin,
)
from apps.lots.models import Lot
from apps.provende.models import Provende, TypeProvende
from apps.reproduction.models import Ponte, Reproduction
from apps.stocks.services import creer_mise_en_stock
from apps.traitement.models import Traitement
from apps.traitement.services import cloturer_traitement, creer_traitement


User = get_user_model()


class LotGrossissementTests(TestCase):
    def setUp(self):
        role = Role.objects.create(libelle="Administrateur")
        self.user = User.objects.create_user(
            username="grossissement-admin",
            password="test-password",
            role=role,
            statut="ACTIF",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.today = timezone.localdate()
        self.bassin_stock = self._bassin("GROSS-STOCK", Bassin.Categorie.STOCKS)
        self.hapa_stock = self._hapa(self.bassin_stock, "STOCK-A")
        self.bassin_destination = self._bassin(
            "GROSS-DEST",
            Bassin.Categorie.GROSSISSEMENT,
        )

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

    def _creer_lot(self, reference, *, parcours=None, espece=Lot.Espece.CARPE):
        bassin_reproduction = self._bassin(
            f"REP-{reference}",
            Bassin.Categorie.REPRODUCTION,
        )
        reproduction = Reproduction.objects.create(
            reference=f"REP-{reference}",
            bassin=bassin_reproduction,
            espece=espece,
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
            espece=espece,
            utilisateur_createur=self.user,
            quantite_initiale=20,
            parcours_production=(
                parcours or Lot.ParcoursProduction.SANS_TRAITEMENT
            ),
        )

    def _mettre_en_stock(
        self,
        reference,
        quantite,
        *,
        traiter=False,
        il_y_a_jours=0,
        espece=Lot.Espece.CARPE,
        hapa=None,
    ):
        lot = self._creer_lot(
            reference,
            parcours=(
                Lot.ParcoursProduction.TRAITEMENT_REQUIS
                if traiter
                else Lot.ParcoursProduction.SANS_TRAITEMENT
            ),
            espece=espece,
        )
        if traiter:
            bassin_traitement = self._bassin(
                f"TRAIT-{reference}",
                Bassin.Categorie.TRAITEMENT,
            )
            hapa_traitement = self._hapa(bassin_traitement, "TRAIT-A")
            type_provende, _ = TypeProvende.objects.get_or_create(
                type=TypeProvende.Type.TRAITEMENT
            )
            provende = Provende.objects.create(
                type_provende=type_provende,
                reference=f"PROV-{reference}",
                date_creation=self.today,
            )
            traitement = creer_traitement(
                {
                    "lot": lot,
                    "bassin": bassin_traitement,
                    "used_provende": provende,
                    "effectif_initial": quantite,
                    "date_ouverture": self.today,
                    "hapas": [{
                        "id_hapa": hapa_traitement.pk,
                        "effectif_initial": quantite,
                    }],
                },
                utilisateur=self.user,
            )
            cloturer_traitement(traitement.pk)

        mise_en_stock = creer_mise_en_stock(
            {
                "lot": lot,
                "bassin": self.bassin_stock,
                "finalite": "GROSSISSEMENT",
                "effectif_entrant": quantite,
                "justification_ecart": "Écart de test",
                "date_mise_en_stock": timezone.now() - timedelta(days=il_y_a_jours),
                "lignes_hapas": [{
                    "hapa": hapa or self.hapa_stock,
                    "effectif_entrant": quantite,
                }],
            },
            utilisateur=self.user,
        )
        return lot, mise_en_stock

    def _creer_sortie(self, lot, *, il_y_a_jours=0, destination=None):
        data = {
            "id_lot": lot.pk,
            "id_bassin_destination": (destination or self.bassin_destination).pk,
        }
        if il_y_a_jours:
            data["date_sortie"] = (
                timezone.now() - timedelta(days=il_y_a_jours)
            ).isoformat()
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

    def _annuler(self, sortie_id, motif="Correction"):
        return self.client.post(
            f"/api/v1/effectifs/sorties-stock/{sortie_id}/annuler/",
            {"motif": motif},
            format="json",
        )

    def _sortie_validee(
        self,
        reference,
        quantite=10,
        *,
        traiter=False,
        entree=None,
        il_y_a_jours=0,
        destination=None,
    ):
        lot, mise_en_stock = self._mettre_en_stock(
            reference,
            quantite,
            traiter=traiter,
            il_y_a_jours=il_y_a_jours + 1 if il_y_a_jours else 0,
        )
        draft = self._creer_sortie(
            lot,
            il_y_a_jours=il_y_a_jours,
            destination=destination,
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        data = {}
        if entree is not None:
            data = {
                "effectif_entrant": entree,
                "justification_ecart": "Écart constaté au comptage",
            }
        validated = self._valider(draft.data["id"], data)
        self.assertEqual(validated.status_code, 200, validated.data)
        return lot, mise_en_stock, draft.data["id"], validated.data

    def test_validation_cree_lot_grossissement_et_change_le_stade(self):
        lot, _, sortie_id, payload = self._sortie_validee("LOT-VALIDATION", 10)
        grossissement = LotGrossissement.objects.get(sortie_stock_id=sortie_id)

        self.assertRegex(grossissement.code, r"^GR-\d{4}-\d{4,}$")
        self.assertEqual(grossissement.lot_origine_id, lot.pk)
        self.assertEqual(grossissement.bassin_id, self.bassin_destination.pk)
        self.assertEqual(grossissement.espece, lot.espece)
        self.assertEqual(grossissement.effectif_initial, 10)
        self.assertEqual(grossissement.statut, LotGrossissement.Statut.ACTIF)
        self.assertIsNone(grossissement.poids_initial_g)
        lot.refresh_from_db()
        self.assertEqual(lot.stade, Lot.Stade.EN_GROSSISSEMENT)

    def test_ecart_effectif_initial_est_base_sur_entree(self):
        lot, _, sortie_id, _ = self._sortie_validee(
            "LOT-ECART",
            10,
            entree=7,
        )
        grossissement = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        self.assertEqual(grossissement.effectif_initial, 7)
        self.assertNotEqual(grossissement.effectif_initial, 10)

    def test_lot_non_en_stock_annule_la_validation_et_ses_mouvements(self):
        lot, _ = self._mettre_en_stock("LOT-MAUVAIS-STade", 10)
        lot.stade = Lot.Stade.CREEE
        lot.save(update_fields=["stade"])
        draft = self._creer_sortie(lot)
        self.assertEqual(draft.status_code, 201, draft.data)
        mouvements_avant = MouvementEffectif.objects.count()

        response = self._valider(draft.data["id"])

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(MouvementEffectif.objects.count(), mouvements_avant)
        self.assertFalse(LotGrossissement.objects.exists())

    def test_annulation_supprime_le_lot_et_restaure_effectifs_et_stade(self):
        lot, _, sortie_id, _ = self._sortie_validee("LOT-ANNULATION", 10)
        response = self._annuler(sortie_id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(LotGrossissement.objects.filter(sortie_stock_id=sortie_id).exists())
        lot.refresh_from_db()
        self.assertEqual(lot.stade, Lot.Stade.EN_STOCK)
        self.assertEqual(
            effectif_vivant(
                self.bassin_stock.pk,
                lot.espece,
                self.today,
                self.hapa_stock.pk,
            ),
            10,
        )

    def test_annulation_refuse_poids_initial_ou_lot_cloture_sans_changement(self):
        lot_a, _, sortie_a, _ = self._sortie_validee("LOT-POIDS", 10)
        gross_a = LotGrossissement.objects.get(sortie_stock_id=sortie_a)
        gross_a.poids_initial_g = Decimal("12.50")
        gross_a.save(update_fields=["poids_initial_g"])
        count_mouvements = MouvementEffectif.objects.count()
        refused_weight = self._annuler(sortie_a)
        self.assertEqual(refused_weight.status_code, 400, refused_weight.data)
        self.assertEqual(MouvementEffectif.objects.count(), count_mouvements)
        self.assertEqual(Lot.objects.get(pk=lot_a.pk).stade, Lot.Stade.EN_GROSSISSEMENT)

        lot_b, _, sortie_b, _ = self._sortie_validee(
            "LOT-CLOTURE-ANNULATION",
            10,
            destination=self._bassin("GROSS-DEST-B", Bassin.Categorie.GROSSISSEMENT),
        )
        gross_b = LotGrossissement.objects.get(sortie_stock_id=sortie_b)
        cloturer_lot_grossissement(
            gross_b.pk,
            motif="Fin du cycle",
            poids_final_g=Decimal("250"),
            utilisateur=self.user,
        )
        count_mouvements = MouvementEffectif.objects.count()
        refused_closed = self._annuler(sortie_b)
        self.assertEqual(refused_closed.status_code, 400, refused_closed.data)
        self.assertEqual(MouvementEffectif.objects.count(), count_mouvements)
        self.assertEqual(
            LotGrossissement.objects.get(pk=gross_b.pk).statut,
            LotGrossissement.Statut.CLOTURE,
        )

    def test_annulation_puis_nouvelle_sortie_recree_un_code_unique(self):
        lot, _, first_id, _ = self._sortie_validee("LOT-CODE-RECREATION", 10)
        first_code = LotGrossissement.objects.get(sortie_stock_id=first_id).code
        self.assertEqual(self._annuler(first_id).status_code, 200)

        draft = self._creer_sortie(lot)
        self.assertEqual(draft.status_code, 201, draft.data)
        validated = self._valider(draft.data["id"])
        self.assertEqual(validated.status_code, 200, validated.data)
        second = LotGrossissement.objects.get(sortie_stock_id=draft.data["id"])

        self.assertNotEqual(first_code, second.code)
        self.assertEqual(LotGrossissement.objects.filter(code=second.code).count(), 1)

    def test_cloture_exige_motif_et_ne_peut_etre_reouverte(self):
        _, _, sortie_id, _ = self._sortie_validee("LOT-CLOTURE", 10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        url = f"/api/v1/grossissement/lots-grossissement/{gross.pk}/cloturer/"

        sans_motif = self.client.post(
            url, {"motif": "  ", "poids_final_g": "250.00"}, format="json"
        )
        cloture = self.client.post(
            url, {"motif": "Fin du cycle", "poids_final_g": "250.00"}, format="json"
        )
        double = self.client.post(
            url, {"motif": "Encore", "poids_final_g": "250.00"}, format="json"
        )

        self.assertEqual(sans_motif.status_code, 400, sans_motif.data)
        self.assertEqual(cloture.status_code, 200, cloture.data)
        self.assertEqual(cloture.data["statut"], LotGrossissement.Statut.CLOTURE)
        self.assertEqual(cloture.data["date_cloture"], self.today.isoformat())
        self.assertEqual(double.status_code, 400, double.data)

    def _url_cloture(self, gross):
        return f"/api/v1/grossissement/lots-grossissement/{gross.pk}/cloturer/"

    def _mortalite(self, quantite, date_evenement):
        return enregistrer_mouvement(
            bassin=self.bassin_destination,
            espece=Lot.Espece.CARPE,
            type_mouvement=MouvementEffectif.TypeMouvement.MORTALITE,
            quantite=quantite,
            date_evenement=date_evenement,
            saisi_par=self.user,
        )

    def test_cloture_exige_poids_final_positif(self):
        _, _, sortie_id, _ = self._sortie_validee("LOT-POIDS-FINAL", 10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)

        for data in ({"motif": "Fin"}, {"motif": "Fin", "poids_final_g": "0"}):
            with self.subTest(data=data):
                refused = self.client.post(self._url_cloture(gross), data, format="json")
                self.assertEqual(refused.status_code, 400, refused.data)
                self.assertIn("poids_final_g", refused.data)
        gross.refresh_from_db()
        self.assertEqual(gross.statut, LotGrossissement.Statut.ACTIF)

    def test_cloture_evacue_effectif_et_cloture_le_lot_origine(self):
        lot, _, sortie_id, _ = self._sortie_validee("LOT-EVACUATION", 10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        self._mortalite(2, self.today)

        cloture = self.client.post(
            self._url_cloture(gross),
            {"motif": "Fin du cycle", "poids_final_g": "250.00"},
            format="json",
        )

        self.assertEqual(cloture.status_code, 200, cloture.data)
        self.assertEqual(cloture.data["effectif_final"], 8)
        self.assertEqual(cloture.data["poids_final_g"], "250.00")
        self.assertTrue(
            MouvementEffectif.objects.filter(
                bassin=self.bassin_destination,
                type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
                quantite=8,
                lot_origine=lot,
            ).exists()
        )
        self.assertEqual(
            effectif_vivant(self.bassin_destination.pk, Lot.Espece.CARPE),
            0,
        )
        self.assertEqual(Lot.objects.get(pk=lot.pk).stade, Lot.Stade.CLOTUREE)

    def test_cloture_refuse_date_future_ou_avant_mise_en_place(self):
        _, _, sortie_id, _ = self._sortie_validee("LOT-DATE-CLOTURE", 10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        mise_en_place = timezone.localtime(gross.date_mise_en_place).date()
        count_mouvements = MouvementEffectif.objects.count()

        for date_cloture in (
            self.today + timedelta(days=1),
            mise_en_place - timedelta(days=1),
        ):
            with self.subTest(date_cloture=date_cloture):
                refused = self.client.post(
                    self._url_cloture(gross),
                    {
                        "motif": "Fin",
                        "poids_final_g": "250",
                        "date_cloture": date_cloture.isoformat(),
                    },
                    format="json",
                )
                self.assertEqual(refused.status_code, 400, refused.data)
                self.assertIn("date_cloture", refused.data)
        self.assertEqual(MouvementEffectif.objects.count(), count_mouvements)

    def test_cloture_effectif_nul_sans_mouvement_sortie(self):
        _, _, sortie_id, _ = self._sortie_validee("LOT-VIDE", 10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        self._mortalite(10, self.today)
        count_mouvements = MouvementEffectif.objects.count()

        cloture = self.client.post(
            self._url_cloture(gross),
            {"motif": "Lot perdu", "poids_final_g": "120"},
            format="json",
        )

        self.assertEqual(cloture.status_code, 200, cloture.data)
        self.assertEqual(cloture.data["effectif_final"], 0)
        self.assertEqual(MouvementEffectif.objects.count(), count_mouvements)

    def test_cloture_avant_mortalites_ulterieures_refusee(self):
        _, _, sortie_id, _ = self._sortie_validee("LOT-RETRO", 10, il_y_a_jours=5)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        self._mortalite(2, self.today - timedelta(days=1))
        count_mouvements = MouvementEffectif.objects.count()

        refused = self.client.post(
            self._url_cloture(gross),
            {
                "motif": "Fin",
                "poids_final_g": "250",
                "date_cloture": (self.today - timedelta(days=3)).isoformat(),
            },
            format="json",
        )

        self.assertEqual(refused.status_code, 400, refused.data)
        gross.refresh_from_db()
        self.assertEqual(gross.statut, LotGrossissement.Statut.ACTIF)
        self.assertEqual(MouvementEffectif.objects.count(), count_mouvements)

    def _sortie_brouillon(self, reference, *, espece=Lot.Espece.CARPE, hapa=None):
        lot, _ = self._mettre_en_stock(reference, 10, espece=espece, hapa=hapa)
        draft = self._creer_sortie(lot)
        self.assertEqual(draft.status_code, 201, draft.data)
        return lot, draft.data["id"]

    def test_creation_refuse_meme_espece_dans_le_bassin(self):
        self._sortie_validee("LOT-CARPE-1", 10)
        lot, sortie_id = self._sortie_brouillon(
            "LOT-CARPE-2",
            hapa=self._hapa(self.bassin_stock, "STOCK-B"),
        )
        count_mouvements = MouvementEffectif.objects.count()

        refused = self._valider(sortie_id)

        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertIn("id_bassin_destination", refused.data)
        self.assertEqual(MouvementEffectif.objects.count(), count_mouvements)
        self.assertEqual(
            SortieStock.objects.get(pk=sortie_id).statut,
            SortieStock.Statut.BROUILLON,
        )
        self.assertEqual(Lot.objects.get(pk=lot.pk).stade, Lot.Stade.EN_STOCK)

    def test_creation_accepte_deux_especes_differentes(self):
        self._sortie_validee("LOT-MIXTE-CARPE", 10)
        _, sortie_id = self._sortie_brouillon(
            "LOT-MIXTE-TILAPIA",
            espece=Lot.Espece.TILAPIA,
            hapa=self._hapa(self.bassin_stock, "STOCK-T"),
        )

        validated = self._valider(sortie_id)

        self.assertEqual(validated.status_code, 200, validated.data)
        self.assertEqual(
            LotGrossissement.objects.filter(
                bassin=self.bassin_destination,
                statut=LotGrossissement.Statut.ACTIF,
            ).count(),
            2,
        )

    def test_creation_accepte_meme_espece_apres_cloture(self):
        _, _, premiere, _ = self._sortie_validee("LOT-AVANT-CLOTURE", 10)
        cloturer_lot_grossissement(
            LotGrossissement.objects.get(sortie_stock_id=premiere).pk,
            motif="Fin du cycle",
            poids_final_g=Decimal("250"),
            utilisateur=self.user,
        )
        _, sortie_id = self._sortie_brouillon(
            "LOT-APRES-CLOTURE",
            hapa=self._hapa(self.bassin_stock, "STOCK-C"),
        )

        validated = self._valider(sortie_id)

        self.assertEqual(validated.status_code, 200, validated.data)

    def test_creation_refuse_bassin_avec_hapa_actif(self):
        _, sortie_id = self._sortie_brouillon("LOT-HAPA-GROSS")
        Hapa.objects.create(
            bassin=self.bassin_destination,
            nom="GROSS-HAPA",
            statut=Hapa.Statut.ACTIF,
        )

        refused = self._valider(sortie_id)

        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertIn("hapa", str(refused.data["id_bassin_destination"]))

    def test_capacite_refuse_un_troisieme_lot(self):
        for reference in ("LOT-CAP-1", "LOT-CAP-2"):
            self._sortie_validee(reference, 10)
            LotGrossissement.objects.filter(
                lot_origine__reference=reference
            ).update(espece=Lot.Espece.TILAPIA if reference.endswith("1") else "AUTRE")
        with self.assertRaises(ValidationError) as ctx:
            verifier_capacite_bassin(self.bassin_destination, Lot.Espece.CARPE)
        self.assertIn("deux lots", str(ctx.exception.detail["id_bassin_destination"]))

    def test_patch_poids_initial(self):
        _, _, sortie_id, _ = self._sortie_validee("LOT-POIDS-INITIAL", 10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        url = f"/api/v1/grossissement/lots-grossissement/{gross.pk}/"

        saisi = self.client.patch(url, {"poids_initial_g": "12.50"}, format="json")
        nul = self.client.patch(url, {"poids_initial_g": "0"}, format="json")

        self.assertEqual(saisi.status_code, 200, saisi.data)
        self.assertEqual(saisi.data["poids_initial_g"], "12.50")
        self.assertEqual(nul.status_code, 400, nul.data)
        self.assertIn("poids_initial_g", nul.data)

        cloturer_lot_grossissement(
            gross.pk,
            motif="Fin du cycle",
            poids_final_g=Decimal("250"),
            utilisateur=self.user,
        )
        clos = self.client.patch(url, {"poids_initial_g": "13"}, format="json")
        self.assertEqual(clos.status_code, 400, clos.data)
        self.assertIn("statut", clos.data)

    def test_patch_modifie_champs_autorises_et_refuse_champs_figes(self):
        _, _, sortie_id, _ = self._sortie_validee("LOT-PATCH", 10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        url = f"/api/v1/grossissement/lots-grossissement/{gross.pk}/"

        changed = self.client.patch(
            url,
            {
                "observation": "Suivi de croissance",
                "pourcentage_groupe_pesee": "25.00",
            },
            format="json",
        )
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data["pourcentage_groupe_pesee"], "25.00")

        for data in (
            {"id_bassin": self.bassin_stock.pk},
            {"effectif_initial": 3},
            {"pourcentage_groupe_pesee": "0"},
            {"pourcentage_groupe_pesee": "100.01"},
        ):
            with self.subTest(data=data):
                refused = self.client.patch(url, data, format="json")
                self.assertEqual(refused.status_code, 400, refused.data)

    def test_age_et_rappels_aux_limites_du_cycle(self):
        _, _, sortie_id, _ = self._sortie_validee("LOT-CYCLE", 10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        for age, etape, prochaine in (
            (0, 1, 30),
            (29, 1, 30),
            (30, 2, 30),
            (75, 4, 75),
            (180, None, None),
        ):
            with self.subTest(age=age):
                gross.date_mise_en_place = timezone.make_aware(
                    datetime.combine(self.today - timedelta(days=age), time.min)
                )
                with patch(
                    "apps.grossissement.models.timezone.localdate",
                    return_value=self.today,
                ):
                    self.assertEqual(gross.age_jours, age)
                    self.assertEqual(gross.etape_courante, etape)
                    expected = (
                        self.today + timedelta(days=prochaine - age)
                        if prochaine is not None
                        else None
                    )
                    self.assertEqual(gross.prochaine_echeance_pesee, expected)

    def test_origine_expose_traitement_stock_et_lot_sans_traitement(self):
        lot_traite, _, sortie_traitee, _ = self._sortie_validee(
            "LOT-ORIGINE-TRAITE",
            10,
            traiter=True,
        )
        lot_sans, _, sortie_sans, _ = self._sortie_validee(
            "LOT-ORIGINE-SANS-TRAITEMENT",
            8,
            destination=self._bassin("GROSS-DEST-B", Bassin.Categorie.GROSSISSEMENT),
        )

        origine_traitee = self.client.get(
            f"/api/v1/grossissement/lots-grossissement/"
            f"{LotGrossissement.objects.get(sortie_stock_id=sortie_traitee).pk}/origine/"
        )
        origine_sans = self.client.get(
            f"/api/v1/grossissement/lots-grossissement/"
            f"{LotGrossissement.objects.get(sortie_stock_id=sortie_sans).pk}/origine/"
        )

        self.assertEqual(origine_traitee.status_code, 200, origine_traitee.data)
        self.assertEqual(origine_traitee.data["lot_origine"]["reference"], lot_traite.reference)
        self.assertIsNotNone(origine_traitee.data["traitement"])
        self.assertEqual(
            origine_traitee.data["mise_en_stock"]["hapas"][0]["effectif_entrant"],
            10,
        )
        self.assertEqual(origine_sans.status_code, 200, origine_sans.data)
        self.assertEqual(origine_sans.data["lot_origine"]["reference"], lot_sans.reference)
        self.assertIsNone(origine_sans.data["traitement"])
