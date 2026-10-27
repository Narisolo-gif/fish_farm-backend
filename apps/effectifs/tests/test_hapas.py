from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.bassins.models import Bassin, Hapa
from apps.core.models import Role
from apps.ecloserie.models import Eclosion
from apps.effectifs.models import MouvementEffectif, SortieStock
from apps.effectifs.services import enregistrer_mouvement
from apps.lots.models import Lot
from apps.reproduction.models import Ponte, Reproduction
from apps.stocks.models import MiseEnStock


User = get_user_model()


class HapaTests(TestCase):
    def setUp(self):
        role, _ = Role.objects.get_or_create(
            libelle="Administrateur",
            defaults={"description": "Tests hapas"},
        )
        self.user = User.objects.create_user(
            username="hapa-admin",
            password="test-password",
            role=role,
            statut="ACTIF",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.today = timezone.localdate()
        self.bassin = self._bassin("HAPA-STOCK")
        self.destination = self._bassin(
            "HAPA-GROSS",
            Bassin.Categorie.GROSSISSEMENT,
        )
        self.hapa = self._hapa(self.bassin, "A")
        self.lot1 = self._lot("HAPA-LOT-1")
        self.lot2 = self._lot("HAPA-LOT-2")
        self._mouvement(self.bassin, self.hapa, self.lot1, "CARPE", 10)
        self._mouvement(self.bassin, self.hapa, self.lot2, "CARPE", 10)

    def _bassin(self, reference, categorie=Bassin.Categorie.STOCKS):
        return Bassin.objects.create(
            reference=reference,
            categorie=categorie,
            capacity=100,
            statut=Bassin.Statut.ACTIF,
        )

    def _hapa(self, bassin, nom, *, actif=True):
        return Hapa.objects.create(
            bassin=bassin,
            nom=nom,
            actif=actif,
            statut=Hapa.Statut.ACTIF if actif else Hapa.Statut.INACTIF,
        )

    def _lot(self, reference, espece="CARPE"):
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
            quantite_oeuf_obtenu=10,
        )
        eclosion = Eclosion.objects.create(
            reference=f"ECLOSION-{reference}",
            ponte=ponte,
            date_eclosion=self.today,
            quantite_alevins_obtenu=10,
        )
        return Lot.objects.create(
            reference=reference,
            eclosion=eclosion,
            date_creation=timezone.now(),
            espece=espece,
            utilisateur_createur=self.user,
            quantite_initiale=10,
            parcours_production=Lot.ParcoursProduction.SANS_TRAITEMENT,
        )

    def _mouvement(self, bassin, hapa, lot, espece, quantite):
        return MouvementEffectif.objects.create(
            bassin=bassin,
            espece=espece,
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            sens=MouvementEffectif.Sens.POSITIF,
            quantite=quantite,
            date_evenement=self.today,
            lot_origine=lot,
            hapa=hapa,
            saisi_par=self.user,
        )

    def _creer_sortie(self, *, quantite=4, composition=None, hapa=None):
        data = {
            "id_bassin_source": self.bassin.pk,
            "id_bassin_destination": self.destination.pk,
            "id_hapa": (hapa or self.hapa).pk,
            "type_sortie": SortieStock.TypeSortie.GROSSISSEMENT,
            "espece": "CARPE",
            "quantite": quantite,
            "date_sortie": timezone.now().isoformat(),
        }
        if composition is not None:
            data["composition"] = composition
        return self.client.post(
            "/api/v1/effectifs/sorties-stock/",
            data,
            format="json",
        )

    def _valider_sortie(self, sortie_id):
        return self.client.post(
            f"/api/v1/effectifs/sorties-stock/{sortie_id}/valider/",
            {},
            format="json",
        )

    def test_mouvement_refuse_hapa_d_un_autre_bassin(self):
        autre_bassin = self._bassin("HAPA-AUTRE")
        hapa_autre = self._hapa(autre_bassin, "AUTRE")
        with self.assertRaises(ValidationError):
            enregistrer_mouvement(
                bassin=self.bassin,
                espece="CARPE",
                type_mouvement="ENTREE",
                quantite=1,
                date_evenement=self.today,
                saisi_par=self.user,
                hapa=hapa_autre,
            )

    def test_mouvement_refuse_hapa_inactif(self):
        hapa_inactif = self._hapa(self.bassin, "INACTIF", actif=False)
        with self.assertRaises(ValidationError):
            enregistrer_mouvement(
                bassin=self.bassin,
                espece="CARPE",
                type_mouvement="ENTREE",
                quantite=1,
                date_evenement=self.today,
                saisi_par=self.user,
                hapa=hapa_inactif,
            )

    def test_sortie_depasse_hapa_meme_si_bassin_suffisant(self):
        self._mouvement(self.bassin, None, self.lot1, "CARPE", 10)
        draft = self._creer_sortie(quantite=11)
        self.assertEqual(draft.status_code, 201, draft.data)
        validated = self._valider_sortie(draft.data["id"])
        self.assertEqual(validated.status_code, 400, validated.data)
        self.assertEqual(
            SortieStock.objects.get(pk=draft.data["id"]).statut,
            SortieStock.Statut.BROUILLON,
        )

    def test_entree_autre_espece_refusee_dans_hapa_occupe(self):
        with self.assertRaises(ValidationError):
            enregistrer_mouvement(
                bassin=self.bassin,
                espece="TILAPIA",
                type_mouvement="ENTREE",
                quantite=1,
                date_evenement=self.today,
                saisi_par=self.user,
                hapa=self.hapa,
            )

    def test_hapa_obligatoire_seulement_si_un_hapa_actif_existe(self):
        with self.assertRaises(ValidationError):
            enregistrer_mouvement(
                bassin=self.bassin,
                espece="CARPE",
                type_mouvement="ENTREE",
                quantite=1,
                date_evenement=self.today,
                saisi_par=self.user,
            )
        bassin_sans_hapa = self._bassin("SANS-HAPA")
        movement = enregistrer_mouvement(
            bassin=bassin_sans_hapa,
            espece="CARPE",
            type_mouvement="ENTREE",
            quantite=1,
            date_evenement=self.today,
            saisi_par=self.user,
        )
        self.assertIsNone(movement.hapa_id)

    def test_sortie_sans_composition_un_lot_cree_une_ligne_exacte(self):
        hapa_unique = self._hapa(self.bassin, "UNIQUE")
        self._mouvement(self.bassin, hapa_unique, self.lot1, "CARPE", 5)
        draft = self._creer_sortie(quantite=3, hapa=hapa_unique)
        self.assertEqual(draft.status_code, 201, draft.data)
        validated = self._valider_sortie(draft.data["id"])
        self.assertEqual(validated.status_code, 200, validated.data)
        self.assertEqual(len(validated.data["composition"]), 1)
        self.assertEqual(validated.data["composition"][0]["id_lot_origine"], self.lot1.pk)
        self.assertEqual(validated.data["composition"][0]["certitude"], "EXACTE")

    def test_sortie_sans_composition_refuse_plusieurs_lots(self):
        draft = self._creer_sortie(quantite=3)
        self.assertEqual(draft.status_code, 201, draft.data)
        validated = self._valider_sortie(draft.data["id"])
        self.assertEqual(validated.status_code, 400, validated.data)
        self.assertIn("composition", validated.data)

    def test_sortie_refuse_composition_de_somme_incorrecte(self):
        draft = self._creer_sortie(
            quantite=5,
            composition=[{"id_lot_origine": self.lot1.pk, "quantite": 4}],
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        validated = self._valider_sortie(draft.data["id"])
        self.assertEqual(validated.status_code, 400, validated.data)
        self.assertIn("composition", validated.data)

    def test_composition_deux_lots_cree_mouvements_par_lot(self):
        draft = self._creer_sortie(
            quantite=5,
            composition=[
                {"id_lot_origine": self.lot1.pk, "quantite": 2},
                {"id_lot_origine": self.lot2.pk, "quantite": 3},
            ],
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        validated = self._valider_sortie(draft.data["id"])
        self.assertEqual(validated.status_code, 200, validated.data)
        self.assertEqual(
            {row["certitude"] for row in validated.data["composition"]},
            {"ESTIMEE"},
        )
        mouvements = MouvementEffectif.objects.filter(
            operation_source_content_type=ContentType.objects.get_for_model(
                SortieStock
            ),
            operation_source_object_id=draft.data["id"],
        )
        sources = mouvements.filter(bassin=self.bassin)
        destinations = mouvements.filter(bassin=self.destination)
        self.assertEqual(sources.count(), 2)
        self.assertEqual(destinations.count(), 2)
        self.assertEqual(
            set(sources.values_list("lot_origine_id", flat=True)),
            {self.lot1.pk, self.lot2.pk},
        )
        self.assertTrue(all(m.hapa_id == self.hapa.pk for m in sources))
        self.assertTrue(all(m.hapa_id is None for m in destinations))

    def test_annulation_annule_tous_les_mouvements_de_la_sortie(self):
        draft = self._creer_sortie(
            quantite=5,
            composition=[
                {"id_lot_origine": self.lot1.pk, "quantite": 2},
                {"id_lot_origine": self.lot2.pk, "quantite": 3},
            ],
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        self.assertEqual(self._valider_sortie(draft.data["id"]).status_code, 200)
        operation_movements = MouvementEffectif.objects.filter(
            operation_source_content_type=ContentType.objects.get_for_model(
                SortieStock
            ),
            operation_source_object_id=draft.data["id"],
        )
        original_ids = set(operation_movements.values_list("id", flat=True))
        cancelled = self.client.post(
            f"/api/v1/effectifs/sorties-stock/{draft.data['id']}/annuler/",
            {"motif": "Correction"},
            format="json",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(
            set(
                MouvementEffectif.objects.filter(
                    annule_mouvement_id__in=original_ids
                ).values_list("annule_mouvement_id", flat=True)
            ),
            original_ids,
        )

    def test_mise_en_stock_refuse_hapa_d_un_autre_bassin(self):
        autre_bassin = self._bassin("STOCK-AUTRE")
        hapa_autre = self._hapa(autre_bassin, "ETRANGER")
        response = self.client.post(
            "/api/v1/stocks/",
            {
                "id_lot": self._lot("MISE-STOCK-LOT").pk,
                "id_bassin": self.bassin.pk,
                "id_hapa": hapa_autre.pk,
                "effectif_entrant": 10,
                "justification_ecart": "",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(MiseEnStock.objects.exists())
