from datetime import datetime, time, timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.bassins.models import Bassin, Hapa
from apps.ecloserie.models import Eclosion
from apps.effectifs.calculs import effectif_vivant
from apps.effectifs.models import (
    CauseMortalite,
    EvenementMortalitePerte,
    MouvementEffectif,
)
from apps.effectifs.services import (
    enregistrer_evenement_mortalite_perte,
    enregistrer_mouvement,
    hapa_est_bloque,
)
from apps.lots.models import Lot
from apps.provende.models import Provende, TypeProvende
from apps.reproduction.models import Ponte, Reproduction
from apps.stocks.models import MiseEnStock, MiseEnStockHapa
from apps.stocks.serializers import MiseEnStockSerializer
from apps.stocks.services import creer_mise_en_stock, supprimer_mise_en_stock
from apps.traitement.models import Traitement
from apps.traitement.services import cloturer_traitement, creer_traitement


User = get_user_model()


class MiseEnStockHapaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="stocks-hapas")
        self.today = timezone.localdate()
        self.stock = self._bassin("STOCK-HAPAS", Bassin.Categorie.STOCKS)
        self.stock_hapa1 = self._hapa(self.stock, "STOCK-A")
        self.stock_hapa2 = self._hapa(self.stock, "STOCK-B")
        self.treatment_bassin = self._bassin(
            "TRAITEMENT-HAPAS",
            Bassin.Categorie.TRAITEMENT,
        )
        self.treatment_hapa1 = self._hapa(self.treatment_bassin, "TRAIT-A")
        self.treatment_hapa2 = self._hapa(self.treatment_bassin, "TRAIT-B")
        self.reproduction_bassin = self._bassin(
            "REPRODUCTION-HAPAS",
            Bassin.Categorie.REPRODUCTION,
        )
        self.reproduction = Reproduction.objects.create(
            reference="REPRODUCTION-HAPAS",
            bassin=self.reproduction_bassin,
            espece=Lot.Espece.CARPE,
            date_ajout=self.today,
            quantite_male=1,
            quantite_femelle=1,
        )
        type_provende, _ = TypeProvende.objects.get_or_create(
            type=TypeProvende.Type.TRAITEMENT,
        )
        self.provende = Provende.objects.create(
            type_provende=type_provende,
            reference="PROVENDE-HAPAS",
            date_creation=self.today,
        )
        self.lot_counter = 0
        self.mortality_counter = 0

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

    def _lot(self, *, quantite=20, espece=Lot.Espece.CARPE):
        self.lot_counter += 1
        suffix = str(self.lot_counter)
        ponte = Ponte.objects.create(
            reference=f"PONTE-STOCK-{suffix}",
            reproduction=self.reproduction,
            date_ponte=self.today,
            quantite_oeuf_obtenu=quantite,
        )
        eclosion = Eclosion.objects.create(
            reference=f"ECLOSION-STOCK-{suffix}",
            ponte=ponte,
            date_eclosion=self.today,
            quantite_alevins_obtenu=quantite,
        )
        return Lot.objects.create(
            reference=f"LOT-STOCK-{suffix}",
            eclosion=eclosion,
            date_creation=timezone.now(),
            espece=espece,
            utilisateur_createur=self.user,
            quantite_initiale=quantite,
            parcours_production=Lot.ParcoursProduction.TRAITEMENT_REQUIS,
        )

    def _traitement(self, *, hapas=True, finals=(6, 6), quantity=20):
        lot = self._lot(quantite=quantity)
        bassin_traitement = self.treatment_bassin
        if not hapas:
            bassin_traitement = self._bassin(
                f"TRAITEMENT-SANS-HAPA-{self.lot_counter}",
                Bassin.Categorie.TRAITEMENT,
            )
        data = {
            "lot": lot,
            "effectif_initial": quantity,
            "date_ouverture": self.today,
            "bassin": bassin_traitement,
            "used_provende": self.provende,
        }
        if hapas:
            data["hapas"] = [
                {"id_hapa": hapa.pk, "effectif_initial": 10}
                for hapa in (self.treatment_hapa1, self.treatment_hapa2)
            ]
        if hapas:
            traitement = creer_traitement(data, self.user)
        else:
            traitement = Traitement.objects.create(
                lot=lot,
                effectif_initial=quantity,
                effectif_final=quantity,
                date_ouverture=self.today,
                date_cloture=self.today,
                bassin=bassin_traitement,
                used_provende=self.provende,
                utilisateur=self.user,
                statut=Traitement.Statut.CLOTUREE,
            )
            lot.stade = Lot.Stade.EN_TRAITEMENT
            lot.save(update_fields=["stade", "updated_at"])
        if hapas:
            for hapa, final_hapa in zip(
                (self.treatment_hapa1, self.treatment_hapa2),
                finals,
            ):
                initial = 10
                if initial > final_hapa:
                    self._mortalite(
                        bassin_traitement,
                        hapa,
                        quantity=initial - final_hapa,
                    )
        traitement = cloturer_traitement(traitement.pk)
        return lot, traitement

    def _creer_mise(
        self,
        lot,
        *,
        lignes=None,
        bassin=None,
        effectif=None,
        finalite=MiseEnStock.Finalite.GROSSISSEMENT,
        **extra,
    ):
        data = {
            "lot": lot,
            "bassin": bassin or self.stock,
            "finalite": finalite,
            "justification_ecart": "",
            **extra,
        }
        if lignes is not None:
            data["lignes_hapas"] = [
                {"hapa": hapa, "effectif_entrant": amount}
                for hapa, amount in lignes
            ]
        if effectif is not None:
            data["effectif_entrant"] = effectif
        return creer_mise_en_stock(data, self.user)

    def _mouvements(self, mise):
        content_type = ContentType.objects.get_for_model(mise)
        return MouvementEffectif.objects.filter(
            operation_source_content_type=content_type,
            operation_source_object_id=mise.pk,
        )

    def _mortalite(
        self,
        bassin,
        hapa,
        espece=Lot.Espece.CARPE,
        quantity=1,
    ):
        self.mortality_counter += 1
        return enregistrer_evenement_mortalite_perte(
            bassin=bassin,
            hapa=hapa,
            espece=espece,
            type_evenement=EvenementMortalitePerte.TypeEvenement.MORTALITE,
            quantite=quantity,
            date_evenement=self.today,
            saisi_par=self.user,
            cause_mortalite=CauseMortalite.objects.create(
                libelle=(
                    f"Cause-{self.lot_counter}-{hapa.pk}-"
                    f"{self.mortality_counter}"
                )
            ),
        )

    def test_total_egal_au_final_cree_sorties_et_entrees_par_hapa(self):
        lot, traitement = self._traitement()
        self.assertTrue(hapa_est_bloque(self.treatment_hapa1.pk))
        self.assertTrue(hapa_est_bloque(self.treatment_hapa2.pk))
        mise = self._creer_mise(
            lot,
            lignes=[(self.stock_hapa1, 6), (self.stock_hapa2, 6)],
            effectif=12,
        )
        mouvements = list(self._mouvements(mise).order_by("id"))
        sorties = [m for m in mouvements if m.type_mouvement == "SORTIE"]
        entrees = [m for m in mouvements if m.type_mouvement == "ENTREE"]
        self.assertEqual(len(sorties), 2)
        self.assertEqual(len(entrees), 2)
        self.assertEqual({m.hapa_id for m in sorties}, {self.treatment_hapa1.pk, self.treatment_hapa2.pk})
        self.assertEqual({m.hapa_id for m in entrees}, {self.stock_hapa1.pk, self.stock_hapa2.pk})
        self.assertTrue(all(m.lot_origine_id == lot.pk for m in mouvements))
        self.assertEqual({m.quantite for m in sorties}, {6})
        self.assertEqual({m.quantite for m in entrees}, {6})
        self.assertEqual(
            Lot.objects.get(pk=lot.pk).stade,
            Lot.Stade.EN_STOCK,
        )
        self.assertEqual(mise.effectif_entrant, traitement.effectif_final)
        self.assertEqual(mise.ecart_effectif, 0)
        self.assertFalse(hapa_est_bloque(self.treatment_hapa1.pk))
        self.assertFalse(hapa_est_bloque(self.treatment_hapa2.pk))
        self.assertEqual(MiseEnStockHapa.objects.filter(mise_en_stock=mise).count(), 2)
        self.assertEqual(
            effectif_vivant(
                self.treatment_bassin.pk,
                lot.espece,
                hapa_id=self.treatment_hapa1.pk,
            ),
            0,
        )
        self.assertEqual(
            effectif_vivant(
                self.treatment_bassin.pk,
                lot.espece,
                hapa_id=self.treatment_hapa2.pk,
            ),
            0,
        )
        self.assertEqual(
            effectif_vivant(self.stock.pk, lot.espece, hapa_id=self.stock_hapa1.pk),
            5,
        )
        self.assertEqual(
            effectif_vivant(self.stock.pk, lot.espece, hapa_id=self.stock_hapa2.pk),
            7,
        )

    def test_total_inferieur_est_justifie_et_ecart_calcule_depuis_les_lignes(self):
        lot, _ = self._traitement()
        serializer = MiseEnStockSerializer(
            data={
                "id_lot": lot.pk,
                "id_bassin": self.stock.pk,
                "finalite": MiseEnStock.Finalite.GROSSISSEMENT,
                "hapas": [
                    {"id_hapa": self.stock_hapa1.pk, "effectif_entrant": 4},
                    {"id_hapa": self.stock_hapa2.pk, "effectif_entrant": 6},
                ],
                "justification_ecart": "Ecart explique par le tri.",
                "ecart_effectif": 999,
            },
            context={"request": SimpleNamespace(user=self.user)},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        mise = serializer.save()
        mouvements = list(self._mouvements(mise))

        self.assertEqual(mise.effectif_entrant, 10)
        self.assertEqual(mise.ecart_effectif, -2)
        self.assertEqual(sum(m.quantite for m in mouvements if m.type_mouvement == "ENTREE"), 10)
        self.assertEqual(
            sum(
                effectif_vivant(self.stock.pk, lot.espece, hapa_id=hapa.pk)
                for hapa in (self.stock_hapa1, self.stock_hapa2)
            ),
            10,
        )
        self.assertEqual(
            effectif_vivant(self.stock.pk, lot.espece, hapa_id=self.stock_hapa1.pk),
            4,
        )
        self.assertEqual(
            effectif_vivant(self.stock.pk, lot.espece, hapa_id=self.stock_hapa2.pk),
            6,
        )
        self.assertFalse(
            any(m.type_mouvement in {"PERTE", "MORTALITE"} for m in mouvements)
        )

    def test_lignes_obligatoires_et_bassin_sans_hapa_refuse(self):
        lot, _ = self._traitement()
        with self.assertRaises(ValidationError) as required:
            self._creer_mise(lot)
        self.assertIn("hapas", required.exception.detail)

        no_hapa_stock = self._bassin("STOCK-SANS-HAPA", Bassin.Categorie.STOCKS)
        with self.assertRaises(ValidationError) as missing:
            self._creer_mise(
                lot,
                bassin=no_hapa_stock,
                lignes=[(self.stock_hapa1, 12)],
                effectif=12,
            )
        self.assertIn("créez d'abord des hapas", str(missing.exception.detail))

    def test_total_superieur_a_la_reference_est_refuse_meme_avec_justification(self):
        lot, _ = self._traitement()
        with self.assertRaises(ValidationError):
            self._creer_mise(
                lot,
                lignes=[(self.stock_hapa1, 7), (self.stock_hapa2, 6)],
                effectif=13,
                justification_ecart="Ne permet pas de dépasser le final.",
            )

        lot_sans_traitement = self._lot(quantite=10)
        with self.assertRaises(ValidationError):
            self._creer_mise(
                lot_sans_traitement,
                lignes=[(self.stock_hapa1, 6), (self.stock_hapa2, 5)],
                effectif=11,
                justification_ecart="Ne permet pas de dépasser la quantité initiale.",
            )

    def test_refuse_si_effectif_hapa_traitement_a_change_apres_cloture(self):
        lot, _ = self._traitement()
        enregistrer_mouvement(
            bassin=self.treatment_bassin,
            hapa=self.treatment_hapa1,
            espece=lot.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
            quantite=1,
            date_evenement=self.today,
            saisi_par=self.user,
            lot_origine=lot,
        )
        with self.assertRaisesMessage(
            ValidationError,
            "L'effectif du hapa de traitement a été modifié après la clôture.",
        ):
            self._creer_mise(
                lot,
                lignes=[(self.stock_hapa1, 6), (self.stock_hapa2, 6)],
                effectif=12,
            )

    def test_ecart_sans_justification_refuse_et_date_historique_acceptee(self):
        lot, _ = self._traitement()
        serializer = MiseEnStockSerializer(
            data={
                "id_lot": lot.pk,
                "id_bassin": self.stock.pk,
                "finalite": MiseEnStock.Finalite.GROSSISSEMENT,
                "hapas": [
                    {"id_hapa": self.stock_hapa1.pk, "effectif_entrant": 5},
                    {"id_hapa": self.stock_hapa2.pk, "effectif_entrant": 6},
                ],
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("justification_ecart", serializer.errors)
        with self.assertRaises(ValidationError) as treated:
            self._creer_mise(
                lot,
                lignes=[(self.stock_hapa1, 5), (self.stock_hapa2, 6)],
                effectif=11,
            )
        self.assertIn("justification_ecart", treated.exception.detail)

        lot_sans_traitement = self._lot(quantite=10)
        with self.assertRaises(ValidationError) as untreated:
            self._creer_mise(
                lot_sans_traitement,
                lignes=[(self.stock_hapa1, 4), (self.stock_hapa2, 5)],
                effectif=9,
            )
        self.assertIn("justification_ecart", untreated.exception.detail)
        mise = self._creer_mise(
            self._lot(quantite=10),
            lignes=[(self.stock_hapa1, 4), (self.stock_hapa2, 5)],
            effectif=9,
            justification_ecart="Ecart justifie pour un lot non traite.",
        )
        self.assertEqual(mise.effectif_entrant, 9)

        date_historique = timezone.make_aware(
            datetime.combine(self.today - timedelta(days=1), time.min)
        )
        lot_historique = self._lot(quantite=10)
        serializer = MiseEnStockSerializer(
            data={
                "id_lot": lot_historique.pk,
                "id_bassin": self.stock.pk,
                "finalite": MiseEnStock.Finalite.GROSSISSEMENT,
                "hapas": [
                    {"id_hapa": self.stock_hapa1.pk, "effectif_entrant": 4},
                    {"id_hapa": self.stock_hapa2.pk, "effectif_entrant": 6},
                ],
                "date_mise_en_stock": date_historique.isoformat(),
            },
            context={"request": SimpleNamespace(user=self.user)},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        mise_historique = serializer.save()
        self.assertEqual(
            timezone.localtime(mise_historique.date_mise_en_stock).date(),
            date_historique.date(),
        )
        self.assertEqual(
            set(
                self._mouvements(mise_historique).values_list(
                    "date_evenement",
                    flat=True,
                )
            ),
            {date_historique.date()},
        )

        date_future = timezone.now() + timedelta(days=1)
        serializer_future = MiseEnStockSerializer(
            data={
                "id_lot": self._lot(quantite=10).pk,
                "id_bassin": self.stock.pk,
                "finalite": MiseEnStock.Finalite.GROSSISSEMENT,
                "hapas": [
                    {"id_hapa": self.stock_hapa1.pk, "effectif_entrant": 4},
                    {"id_hapa": self.stock_hapa2.pk, "effectif_entrant": 6},
                ],
                "date_mise_en_stock": date_future.isoformat(),
            }
        )
        self.assertFalse(serializer_future.is_valid())
        self.assertIn("date_mise_en_stock", serializer_future.errors)
        with self.assertRaises(ValidationError) as future:
            self._creer_mise(
                self._lot(quantite=10),
                lignes=[(self.stock_hapa1, 4), (self.stock_hapa2, 6)],
                effectif=10,
                date_mise_en_stock=date_future,
            )
        self.assertIn("date_mise_en_stock", future.exception.detail)

    def test_traitement_cloture_sans_lignes_hapas_est_refuse(self):
        lot, _ = self._traitement(hapas=False, quantity=10)
        with self.assertRaises(ValidationError) as missing:
            self._creer_mise(
                lot,
                lignes=[(self.stock_hapa1, 10)],
                effectif=10,
            )
        self.assertIn(
            "Traitement sans hapas : supprimez-le et recréez-le.",
            str(missing.exception.detail),
        )

    def test_suppression_annule_mouvements_et_retablit_stade_precedent(self):
        lot, _ = self._traitement()
        mise = self._creer_mise(
            lot,
            lignes=[(self.stock_hapa1, 6), (self.stock_hapa2, 6)],
            effectif=12,
        )
        supprimer_mise_en_stock(mise.pk)
        self.assertFalse(MiseEnStock.objects.filter(pk=mise.pk).exists())
        self.assertEqual(
            Lot.objects.get(pk=lot.pk).stade,
            Lot.Stade.EN_TRAITEMENT,
        )
        self.assertTrue(hapa_est_bloque(self.treatment_hapa1.pk))
        self.assertEqual(
            self._mouvements(mise).filter(type_mouvement="ANNULATION").count(),
            4,
        )
        self.assertEqual(
            effectif_vivant(self.stock.pk, lot.espece, hapa_id=self.stock_hapa1.pk),
            0,
        )
        self.assertEqual(
            effectif_vivant(
                self.treatment_bassin.pk,
                lot.espece,
                hapa_id=self.treatment_hapa1.pk,
            ),
            6,
        )
        self.assertEqual(
            effectif_vivant(
                self.treatment_bassin.pk,
                lot.espece,
                hapa_id=self.treatment_hapa2.pk,
            ),
            6,
        )

    def test_finalite_obligatoire_a_la_creation_et_immuable_en_patch(self):
        lot = self._lot(quantite=10)
        sans_finalite = MiseEnStockSerializer(
            data={
                "id_lot": lot.pk,
                "id_bassin": self.stock.pk,
                "hapas": [
                    {"id_hapa": self.stock_hapa1.pk, "effectif_entrant": 10},
                ],
            }
        )
        self.assertFalse(sans_finalite.is_valid())
        self.assertIn("finalite", sans_finalite.errors)

        mise = self._creer_mise(
            lot,
            lignes=[(self.stock_hapa1, 10)],
            finalite=MiseEnStock.Finalite.VENTE,
        )
        serializer_patch = MiseEnStockSerializer(
            instance=mise,
            data={"finalite": MiseEnStock.Finalite.GROSSISSEMENT},
            partial=True,
        )
        self.assertFalse(serializer_patch.is_valid())
        self.assertIn("finalite", serializer_patch.errors)
        self.assertEqual(mise.finalite, MiseEnStock.Finalite.VENTE)

    def test_finalite_des_lots_dans_hapa_vide_ou_occupe(self):
        premier_lot = self._lot(quantite=10)
        mise_vente = self._creer_mise(
            premier_lot,
            lignes=[(self.stock_hapa1, 10)],
            finalite=MiseEnStock.Finalite.VENTE,
        )
        enregistrer_mouvement(
            bassin=self.stock,
            hapa=self.stock_hapa1,
            espece=premier_lot.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
            quantite=10,
            date_evenement=self.today,
            saisi_par=self.user,
            lot_origine=premier_lot,
        )
        deuxieme_lot = self._lot(quantite=10)
        mise_grossissement = self._creer_mise(
            deuxieme_lot,
            lignes=[(self.stock_hapa1, 10)],
            finalite=MiseEnStock.Finalite.GROSSISSEMENT,
        )
        self.assertEqual(
            MiseEnStockHapa.objects.get(
                mise_en_stock=mise_grossissement,
            ).hapa,
            self.stock_hapa1,
        )

        lot_occupe = self._lot(quantite=10)
        self._creer_mise(
            lot_occupe,
            lignes=[(self.stock_hapa2, 10)],
            finalite=MiseEnStock.Finalite.GROSSISSEMENT,
        )
        lot_mismatch = self._lot(quantite=10)
        with self.assertRaises(ValidationError) as mismatch:
            self._creer_mise(
                lot_mismatch,
                lignes=[(self.stock_hapa2, 10)],
                finalite=MiseEnStock.Finalite.VENTE,
            )
        self.assertIn("hapas", mismatch.exception.detail)
        self.assertIn("finalité Grossissement", str(mismatch.exception.detail))

        lot_meme_finalite = self._lot(quantite=10)
        meme_finalite = self._creer_mise(
            lot_meme_finalite,
            lignes=[(self.stock_hapa2, 10)],
            finalite=MiseEnStock.Finalite.GROSSISSEMENT,
        )
        self.assertEqual(
            meme_finalite.finalite,
            MiseEnStock.Finalite.GROSSISSEMENT,
        )

        hapa_legacy = self._hapa(self.stock, "STOCK-LEGACY")
        lot_legacy = self._lot(quantite=10)
        MiseEnStock.objects.create(
            lot=lot_legacy,
            bassin=self.stock,
            finalite=None,
            effectif_entrant=10,
            utilisateur=self.user,
        )
        enregistrer_mouvement(
            bassin=self.stock,
            hapa=hapa_legacy,
            espece=lot_legacy.espece,
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            quantite=10,
            date_evenement=self.today,
            saisi_par=self.user,
            lot_origine=lot_legacy,
        )
        with self.assertRaises(ValidationError) as sans_finalite:
            self._creer_mise(
                self._lot(quantite=10),
                lignes=[(hapa_legacy, 10)],
                finalite=MiseEnStock.Finalite.GROSSISSEMENT,
            )
        self.assertIn("sans finalité", str(sans_finalite.exception.detail))
