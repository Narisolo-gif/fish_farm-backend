from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.bassins.models import Bassin, Hapa
from apps.core.models import Role
from apps.ecloserie.models import Eclosion
from apps.effectifs.models import MouvementEffectif
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

    def test_hapa_obligatoire_ou_interdit_selon_la_categorie(self):
        treatment = self._bassin("TRAITEMENT-SANS-HAPA", Bassin.Categorie.TRAITEMENT)
        stock = self._bassin("STOCK-SANS-HAPA", Bassin.Categorie.STOCKS)
        for bassin in (treatment, stock):
            with self.subTest(categorie=bassin.categorie):
                with self.assertRaises(ValidationError) as error:
                    enregistrer_mouvement(
                        bassin=bassin,
                        espece="CARPE",
                        type_mouvement="ENTREE",
                        quantite=1,
                        date_evenement=self.today,
                        saisi_par=self.user,
                    )
                self.assertIn("id_hapa", error.exception.detail)

        for category in (
            Bassin.Categorie.REPRODUCTION,
            Bassin.Categorie.GROSSISSEMENT,
        ):
            bassin = self._bassin(f"{category}-SANS-HAPA", category)
            movement = enregistrer_mouvement(
                bassin=bassin,
                espece="CARPE",
                type_mouvement="ENTREE",
                quantite=1,
                date_evenement=self.today,
                saisi_par=self.user,
            )
            self.assertIsNone(movement.hapa_id)

        # Reproduction : hapa facultatif, donc accepté s'il est fourni.
        reproduction = Bassin.objects.get(
            reference=f"{Bassin.Categorie.REPRODUCTION}-SANS-HAPA"
        )
        hapa_reproduction = self._hapa(reproduction, "HAPA-REPRODUCTION")
        movement = enregistrer_mouvement(
            bassin=reproduction,
            espece="CARPE",
            type_mouvement="ENTREE",
            quantite=1,
            date_evenement=self.today,
            saisi_par=self.user,
            hapa=hapa_reproduction,
        )
        self.assertEqual(movement.hapa_id, hapa_reproduction.pk)

        # Grossissement : hapa toujours interdit.
        grossissement = Bassin.objects.get(
            reference=f"{Bassin.Categorie.GROSSISSEMENT}-SANS-HAPA"
        )
        hapa_grossissement = self._hapa(grossissement, "HAPA-GROSSISSEMENT")
        with self.assertRaises(ValidationError) as error:
            enregistrer_mouvement(
                bassin=grossissement,
                espece="CARPE",
                type_mouvement="ENTREE",
                quantite=1,
                date_evenement=self.today,
                saisi_par=self.user,
                hapa=hapa_grossissement,
            )
        self.assertIn("id_hapa", error.exception.detail)

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
