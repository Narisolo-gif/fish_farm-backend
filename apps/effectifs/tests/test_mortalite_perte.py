from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.bassins.models import Bassin, Hapa
from apps.ecloserie.models import Eclosion
from apps.effectifs.calculs import effectif_vivant
from apps.effectifs.models import (
    CategoriePerte,
    CauseMortalite,
    EvenementMortalitePerte,
    MouvementEffectif,
)
from apps.effectifs.services import (
    annuler_evenement,
    enregistrer_evenement,
    enregistrer_mouvement,
    hapa_est_bloque,
    totaux_evenements,
)
from apps.lots.models import Lot
from apps.provende.models import Provende, TypeProvende
from apps.reproduction.models import Ponte, Reproduction
from apps.stocks.services import creer_mise_en_stock
from apps.traitement.models import TraitementHapa
from apps.traitement.services import cloturer_traitement, creer_traitement


User = get_user_model()


class MortalitesPertesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="journal-mortalite")
        self.today = timezone.localdate()
        self.treatment = self._bassin("TRAITEMENT", Bassin.Categorie.TRAITEMENT)
        self.treatment_hapa = self._hapa(self.treatment, "T-A")
        self.treatment_hapa2 = self._hapa(self.treatment, "T-B")
        self.stock = self._bassin("STOCK", Bassin.Categorie.STOCKS)
        self.stock_hapa = self._hapa(self.stock, "S-A")
        self.stock_hapa2 = self._hapa(self.stock, "S-B")
        self.reproduction = self._bassin(
            "REPRODUCTION",
            Bassin.Categorie.REPRODUCTION,
        )
        self.grossissement = self._bassin(
            "GROSSISSEMENT",
            Bassin.Categorie.GROSSISSEMENT,
        )
        self.reference_bassin = self._bassin(
            "REFERENCE",
            Bassin.Categorie.REPRODUCTION,
        )
        self.cause = CauseMortalite.objects.get_or_create(
            libelle="Inconnue"
        )[0]
        self.categorie = CategoriePerte.objects.get_or_create(
            libelle="Autre"
        )[0]
        type_provende, _ = TypeProvende.objects.get_or_create(
            type=TypeProvende.Type.TRAITEMENT,
        )
        self.provende = Provende.objects.create(
            type_provende=type_provende,
            reference="PROVENDE-JOURNAL",
            date_creation=self.today,
        )
        self.reproduction_lots = Reproduction.objects.create(
            reference="REPRO-JOURNAL",
            bassin=self.reproduction,
            espece=Lot.Espece.CARPE,
            date_ajout=self.today,
            quantite_male=1,
            quantite_femelle=1,
        )
        self.lot_counter = 0

    def _bassin(self, reference, categorie):
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

    def _lot(self, espece=Lot.Espece.CARPE, quantite=10):
        self.lot_counter += 1
        suffix = str(self.lot_counter)
        ponte = Ponte.objects.create(
            reference=f"PONTE-JOURNAL-{suffix}",
            reproduction=self.reproduction_lots,
            date_ponte=self.today,
            quantite_oeuf_obtenu=quantite,
        )
        eclosion = Eclosion.objects.create(
            reference=f"ECLOSION-JOURNAL-{suffix}",
            ponte=ponte,
            date_eclosion=self.today,
            quantite_alevins_obtenu=quantite,
        )
        return Lot.objects.create(
            reference=f"LOT-JOURNAL-{suffix}",
            eclosion=eclosion,
            date_creation=timezone.now(),
            espece=espece,
            utilisateur_createur=self.user,
            quantite_initiale=quantite,
            parcours_production=Lot.ParcoursProduction.TRAITEMENT_REQUIS,
        )

    def _creer_traitement(self, *, initial=10):
        lot = self._lot(quantite=initial)
        moitie = initial // 2
        traitement = creer_traitement(
            {
                "lot": lot,
                "effectif_initial": initial,
                "date_ouverture": self.today,
                "bassin": self.treatment,
                "used_provende": self.provende,
                "hapas": [
                    {
                        "id_hapa": self.treatment_hapa.pk,
                        "effectif_initial": moitie,
                    },
                    {
                        "id_hapa": self.treatment_hapa2.pk,
                        "effectif_initial": initial - moitie,
                    },
                ],
            },
            self.user,
        )
        return lot, traitement

    def _event_data(
        self,
        bassin,
        *,
        hapa=None,
        espece=Lot.Espece.CARPE,
        quantity=1,
        event_type=EvenementMortalitePerte.TypeEvenement.MORTALITE,
        date=None,
        cause=None,
        category=None,
        justification="",
    ):
        return {
            "bassin": bassin,
            "hapa": hapa,
            "espece": espece,
            "type_evenement": event_type,
            "quantite": quantity,
            "date_evenement": date or timezone.now(),
            "cause_mortalite": cause,
            "categorie_perte": category,
            "justification": justification,
        }

    def _ajouter_effectif(self, bassin, espece, quantity, *, hapa=None, lot=None):
        return enregistrer_mouvement(
            bassin=bassin,
            hapa=hapa,
            espece=espece,
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            quantite=quantity,
            date_evenement=self.today,
            saisi_par=self.user,
            lot_origine=lot,
        )

    def test_traitement_mortalite_enregistre_mouvement_et_lot_origine(self):
        lot, _ = self._creer_traitement()
        event = enregistrer_evenement(
            self._event_data(
                self.treatment,
                hapa=self.treatment_hapa,
                quantity=2,
                cause=self.cause,
            ),
            self.user,
        )

        self.assertEqual(event.mouvement.hapa_id, self.treatment_hapa.pk)
        self.assertEqual(event.mouvement.type_mouvement, "MORTALITE")
        self.assertEqual(event.mouvement.lot_origine_id, lot.pk)
        self.assertEqual(
            effectif_vivant(
                self.treatment.pk,
                Lot.Espece.CARPE,
                hapa_id=self.treatment_hapa.pk,
            ),
            3,
        )

    def test_referentiels_et_justification_obligatoires(self):
        cases = [
            self._event_data(
                self.treatment,
                hapa=self.treatment_hapa,
                event_type=EvenementMortalitePerte.TypeEvenement.PERTE,
                category=self.categorie,
            ),
            self._event_data(
                self.treatment,
                hapa=self.treatment_hapa,
                cause=None,
            ),
            self._event_data(
                self.treatment,
                hapa=self.treatment_hapa,
                cause=self.cause,
                category=self.categorie,
            ),
        ]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(ValidationError):
                    enregistrer_evenement(data, self.user)
        self.assertFalse(EvenementMortalitePerte.objects.exists())

    def test_quantite_superieure_a_effectif_hapa_refuse_sans_ecriture(self):
        self._ajouter_effectif(
            self.treatment,
            Lot.Espece.CARPE,
            3,
            hapa=self.treatment_hapa,
        )
        with self.assertRaises(ValidationError) as error:
            enregistrer_evenement(
                self._event_data(
                    self.treatment,
                    hapa=self.treatment_hapa,
                    quantity=4,
                    cause=self.cause,
                ),
                self.user,
            )
        self.assertIn("quantite", error.exception.detail)
        self.assertFalse(EvenementMortalitePerte.objects.exists())
        self.assertEqual(MouvementEffectif.objects.count(), 1)

    def test_hapa_obligatoire_ou_interdit_selon_categorie(self):
        for bassin in (self.treatment, self.stock):
            with self.subTest(categorie=bassin.categorie):
                with self.assertRaises(ValidationError) as error:
                    enregistrer_evenement(
                        self._event_data(bassin, cause=self.cause),
                        self.user,
                    )
                self.assertIn("id_hapa", error.exception.detail)

        hapa_grossissement = self._hapa(self.grossissement, "H-GROSS")
        with self.assertRaises(ValidationError) as error:
            enregistrer_evenement(
                self._event_data(
                    self.grossissement,
                    hapa=hapa_grossissement,
                    cause=self.cause,
                ),
                self.user,
            )
        self.assertIn("id_hapa", error.exception.detail)

    def test_hapa_facultatif_en_reproduction(self):
        hapa = self._hapa(self.reproduction, "H-REPRO")
        self._ajouter_effectif(self.reproduction, Lot.Espece.CARPE, 5, hapa=hapa)
        self._ajouter_effectif(self.reproduction, Lot.Espece.CARPE, 5)

        avec_hapa = enregistrer_evenement(
            self._event_data(self.reproduction, hapa=hapa, cause=self.cause),
            self.user,
        )
        sans_hapa = enregistrer_evenement(
            self._event_data(self.reproduction, cause=self.cause),
            self.user,
        )

        self.assertEqual(avec_hapa.hapa_id, hapa.pk)
        self.assertIsNone(sans_hapa.hapa_id)

    def test_hapa_autre_bassin_ou_inactif_refuse(self):
        other_treatment = self._bassin(
            "TRAITEMENT-AUTRE",
            Bassin.Categorie.TRAITEMENT,
        )
        other_hapa = self._hapa(other_treatment, "AUTRE")
        inactive_hapa = self._hapa(
            self.treatment,
            "INACTIF",
            actif=False,
        )
        for hapa in (other_hapa, inactive_hapa):
            with self.subTest(hapa=hapa.pk):
                with self.assertRaises(ValidationError) as error:
                    enregistrer_evenement(
                        self._event_data(
                            self.treatment,
                            hapa=hapa,
                            cause=self.cause,
                        ),
                        self.user,
                    )
                self.assertIn("id_hapa", error.exception.detail)

    def test_date_future_refusee(self):
        self._ajouter_effectif(
            self.treatment,
            Lot.Espece.CARPE,
            3,
            hapa=self.treatment_hapa,
        )
        with self.assertRaises(ValidationError) as error:
            enregistrer_evenement(
                self._event_data(
                    self.treatment,
                    hapa=self.treatment_hapa,
                    cause=self.cause,
                    date=timezone.now() + timedelta(days=1),
                ),
                self.user,
            )
        self.assertIn("date_evenement", error.exception.detail)

    def test_espece_omise_deduite_si_unique_et_refuse_si_ambigue(self):
        self._ajouter_effectif(
            self.reproduction,
            Lot.Espece.CARPE,
            3,
        )
        event = enregistrer_evenement(
            self._event_data(
                self.reproduction,
                espece=None,
                cause=self.cause,
            ),
            self.user,
        )
        self.assertEqual(event.espece, Lot.Espece.CARPE)

        self._ajouter_effectif(
            self.reference_bassin,
            Lot.Espece.CARPE,
            2,
        )
        self._ajouter_effectif(
            self.reference_bassin,
            Lot.Espece.TILAPIA,
            2,
        )
        with self.assertRaises(ValidationError) as error:
            enregistrer_evenement(
                self._event_data(
                    self.reference_bassin,
                    espece=None,
                    cause=self.cause,
                ),
                self.user,
            )
        self.assertIn("précisez l'espèce", str(error.exception.detail))

    def test_mortalite_avant_cloture_diminue_effectif_final(self):
        _, traitement = self._creer_traitement()
        enregistrer_evenement(
            self._event_data(
                self.treatment,
                hapa=self.treatment_hapa,
                quantity=2,
                cause=self.cause,
            ),
            self.user,
        )
        traitement = cloturer_traitement(traitement.pk)
        effectifs = dict(
            TraitementHapa.objects.filter(traitement=traitement)
            .values_list("hapa_id", "effectif_final")
        )

        self.assertEqual(traitement.effectif_final, 8)
        self.assertEqual(effectifs[self.treatment_hapa.pk], 3)

    def test_cloture_bloque_jusqua_mise_en_stock_puis_leve(self):
        lot, traitement = self._creer_traitement()
        traitement = cloturer_traitement(traitement.pk)
        self.assertTrue(hapa_est_bloque(self.treatment_hapa.pk))
        with self.assertRaises(ValidationError) as error:
            enregistrer_evenement(
                self._event_data(
                    self.treatment,
                    hapa=self.treatment_hapa,
                    cause=self.cause,
                ),
                self.user,
            )
        self.assertIn("id_hapa", error.exception.detail)
        self.assertIn("Hapa bloqué", str(error.exception.detail))

        creer_mise_en_stock(
            {
                "lot": lot,
                "bassin": self.stock,
                "lignes_hapas": [
                    {"hapa": self.stock_hapa, "effectif_entrant": 5},
                    {"hapa": self.stock_hapa2, "effectif_entrant": 5},
                ],
                "justification_ecart": "",
            },
            self.user,
        )
        self.assertFalse(hapa_est_bloque(self.treatment_hapa.pk))
        self.assertFalse(hapa_est_bloque(self.treatment_hapa2.pk))

    def test_annulation_restaure_effectif_et_refuse_motif_vide_ou_doublon(self):
        self._ajouter_effectif(
            self.treatment,
            Lot.Espece.CARPE,
            5,
            hapa=self.treatment_hapa,
        )
        event = enregistrer_evenement(
            self._event_data(
                self.treatment,
                hapa=self.treatment_hapa,
                quantity=2,
                cause=self.cause,
            ),
            self.user,
        )
        with self.assertRaises(ValidationError):
            annuler_evenement(event.pk, "  ", self.user)
        annuler_evenement(event.pk, "Saisie incorrecte", self.user)
        event.refresh_from_db()
        self.assertEqual(event.statut, EvenementMortalitePerte.Statut.ANNULEE)
        self.assertEqual(
            effectif_vivant(
                self.treatment.pk,
                Lot.Espece.CARPE,
                hapa_id=self.treatment_hapa.pk,
            ),
            5,
        )
        with self.assertRaises(ValidationError):
            annuler_evenement(event.pk, "Nouvelle tentative", self.user)

    def test_mortalite_grossissement_sans_hapa_utilise_registre(self):
        self._ajouter_effectif(
            self.grossissement,
            Lot.Espece.TILAPIA,
            6,
        )
        event = enregistrer_evenement(
            self._event_data(
                self.grossissement,
                espece=Lot.Espece.TILAPIA,
                quantity=2,
                cause=self.cause,
            ),
            self.user,
        )
        self.assertIsNone(event.hapa_id)
        self.assertIsNone(event.mouvement.hapa_id)
        self.assertEqual(
            effectif_vivant(self.grossissement.pk, Lot.Espece.TILAPIA),
            4,
        )

    def test_totaux_excluent_les_evenements_annules(self):
        self._ajouter_effectif(
            self.treatment,
            Lot.Espece.CARPE,
            5,
            hapa=self.treatment_hapa,
        )
        event_annule = enregistrer_evenement(
            self._event_data(
                self.treatment,
                hapa=self.treatment_hapa,
                quantity=1,
                cause=self.cause,
            ),
            self.user,
        )
        annuler_evenement(event_annule.pk, "Correction", self.user)
        enregistrer_evenement(
            self._event_data(
                self.treatment,
                hapa=self.treatment_hapa,
                quantity=2,
                event_type=EvenementMortalitePerte.TypeEvenement.PERTE,
                category=self.categorie,
                justification="Disparition vérifiée",
            ),
            self.user,
        )

        totaux = totaux_evenements({})
        self.assertEqual(totaux["par_type"], [{"type_evenement": "PERTE", "total": 2}])
        self.assertEqual(len(totaux["par_cause"]), 0)
        self.assertEqual(totaux["par_categorie"][0]["total"], 2)
