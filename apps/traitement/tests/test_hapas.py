from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.bassins.models import Bassin, Hapa
from apps.ecloserie.models import Eclosion
from apps.effectifs.models import (
    CauseMortalite,
    EvenementMortalitePerte,
    MouvementEffectif,
)
from apps.effectifs.services import (
    enregistrer_evenement_mortalite_perte,
    enregistrer_mouvement,
    enregistrer_mouvement_operation,
)
from apps.lots.models import Lot
from apps.provende.models import Provende, TypeProvende
from apps.reproduction.models import Ponte, Reproduction
from apps.traitement.models import Traitement, TraitementHapa
from apps.traitement.services import (
    cloturer_traitement,
    creer_traitement,
    supprimer_traitement,
)


User = get_user_model()


class TraitementHapaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="traitement-hapa")
        self.today = timezone.localdate()
        self.bassin = self._bassin("TRAITEMENT-HAPAS")
        self.hapa1 = self._hapa(self.bassin, "A")
        self.hapa2 = self._hapa(self.bassin, "B")

        bassin_reproduction = self._bassin(
            "REPRODUCTION-HAPAS",
            Bassin.Categorie.REPRODUCTION,
        )
        self.reproduction = Reproduction.objects.create(
            reference="REPRODUCTION-HAPAS",
            bassin=bassin_reproduction,
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

    def _bassin(self, reference, categorie=Bassin.Categorie.TRAITEMENT):
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

    def _lot(self, *, quantite=20):
        self.lot_counter += 1
        suffix = str(self.lot_counter)
        ponte = Ponte.objects.create(
            reference=f"PONTE-HAPAS-{suffix}",
            reproduction=self.reproduction,
            date_ponte=self.today,
            quantite_oeuf_obtenu=quantite,
        )
        eclosion = Eclosion.objects.create(
            reference=f"ECLOSION-HAPAS-{suffix}",
            ponte=ponte,
            date_eclosion=self.today,
            quantite_alevins_obtenu=quantite,
        )
        return Lot.objects.create(
            reference=f"LOT-HAPAS-{suffix}",
            eclosion=eclosion,
            date_creation=timezone.now(),
            espece=Lot.Espece.CARPE,
            utilisateur_createur=self.user,
            quantite_initiale=quantite,
            parcours_production=Lot.ParcoursProduction.TRAITEMENT_REQUIS,
        )

    def _creer_traitement(
        self,
        *,
        lot=None,
        bassin=None,
        effectif_initial=10,
        hapas=None,
    ):
        return creer_traitement(
            {
                "lot": lot or self._lot(),
                "bassin": bassin or self.bassin,
                "used_provende": self.provende,
                "date_ouverture": self.today,
                "effectif_initial": effectif_initial,
                **({"hapas": hapas} if hapas is not None else {}),
            },
            utilisateur=self.user,
        )

    def _mouvement(self, bassin, hapa, quantity, *, movement_type=None):
        return enregistrer_mouvement(
            bassin=bassin,
            hapa=hapa,
            espece=Lot.Espece.CARPE,
            type_mouvement=(
                movement_type or MouvementEffectif.TypeMouvement.ENTREE
            ),
            quantite=quantity,
            date_evenement=self.today,
            saisi_par=self.user,
        )

    def _hapas_data(self, rows=None):
        rows = rows or [(self.hapa1, 4), (self.hapa2, 6)]
        return [
            {"id_hapa": hapa.pk, "effectif_initial": quantity}
            for hapa, quantity in rows
        ]

    def test_creation_cree_une_entree_par_hapa(self):
        traitement = self._creer_traitement(hapas=self._hapas_data())
        content_type = ContentType.objects.get_for_model(Traitement)
        mouvements = MouvementEffectif.objects.filter(
            operation_source_content_type=content_type,
            operation_source_object_id=traitement.pk,
        )

        self.assertEqual(mouvements.count(), 2)
        self.assertEqual(
            sum(mouvement.quantite for mouvement in mouvements),
            traitement.effectif_initial,
        )
        for mouvement in mouvements:
            self.assertEqual(
                mouvement.type_mouvement,
                MouvementEffectif.TypeMouvement.ENTREE,
            )
            self.assertIsNotNone(mouvement.hapa_id)
            self.assertEqual(mouvement.lot_origine_id, traitement.lot_id)

    def test_creation_refuse_une_somme_differente(self):
        with self.assertRaises(ValidationError) as error:
            self._creer_traitement(hapas=self._hapas_data([(self.hapa1, 9)]))

        self.assertIn("hapas", error.exception.detail)
        self.assertFalse(Traitement.objects.exists())

    def test_hapas_obligatoires_ou_interdits_selon_le_bassin(self):
        with self.assertRaises(ValidationError) as required:
            self._creer_traitement()
        self.assertIn("hapas", required.exception.detail)

        bassin_sans_hapa = self._bassin("TRAITEMENT-SANS-HAPA")
        with self.assertRaises(ValidationError) as forbidden:
            self._creer_traitement(
                bassin=bassin_sans_hapa,
                hapas=[],
            )
        self.assertIn("hapas", forbidden.exception.detail)

    def test_creation_refuse_hapa_autre_bassin_inactif_ou_occupe(self):
        hapa_autre_bassin = self._hapa(
            self._bassin("TRAITEMENT-AUTRE"),
            "AUTRE",
        )
        hapa_inactif = self._hapa(self.bassin, "INACTIF", actif=False)
        self._mouvement(self.bassin, self.hapa1, 1)

        for hapa in (hapa_autre_bassin, hapa_inactif, self.hapa1):
            with self.subTest(hapa=hapa.nom):
                with self.assertRaises(ValidationError) as error:
                    self._creer_traitement(
                        hapas=self._hapas_data([(hapa, 10)]),
                    )
                self.assertIn("hapas", error.exception.detail)

    def test_creation_refuse_un_hapa_en_double(self):
        with self.assertRaises(ValidationError) as error:
            self._creer_traitement(
                hapas=self._hapas_data(
                    [(self.hapa1, 4), (self.hapa1, 6)]
                ),
            )

        self.assertIn("hapas", error.exception.detail)

    def test_cloture_calcule_les_effectifs_apres_mortalite(self):
        traitement = self._creer_traitement(hapas=self._hapas_data())
        cause = CauseMortalite.objects.create(libelle="Cause hapas")
        for hapa, quantity in ((self.hapa1, 1), (self.hapa2, 2)):
            enregistrer_evenement_mortalite_perte(
                bassin=self.bassin,
                hapa=hapa,
                espece=Lot.Espece.CARPE,
                type_evenement=EvenementMortalitePerte.TypeEvenement.MORTALITE,
                quantite=quantity,
                date_evenement=self.today,
                saisi_par=self.user,
                cause_mortalite=cause,
            )

        traitement = cloturer_traitement(traitement.pk)

        self.assertEqual(traitement.effectif_final, 7)
        self.assertEqual(
            dict(
                TraitementHapa.objects.filter(traitement=traitement)
                .values_list("hapa_id", "effectif_final")
            ),
            {self.hapa1.pk: 3, self.hapa2.pk: 4},
        )

    def test_cloture_sans_evenement_fige_les_effectifs_initiaux(self):
        traitement = self._creer_traitement(hapas=self._hapas_data())

        traitement = cloturer_traitement(traitement.pk)

        self.assertEqual(traitement.effectif_final, 10)
        self.assertEqual(
            set(
                TraitementHapa.objects.filter(traitement=traitement)
                .values_list("effectif_final", flat=True)
            ),
            {4, 6},
        )

    def test_cloture_refuse_un_effectif_superieur_a_l_initial(self):
        traitement = self._creer_traitement(hapas=self._hapas_data())
        self._mouvement(
            self.bassin,
            self.hapa1,
            1,
            movement_type=MouvementEffectif.TypeMouvement.AJUSTEMENT_POSITIF,
        )

        with self.assertRaises(ValidationError) as error:
            cloturer_traitement(traitement.pk)

        self.assertIn("effectif_final", error.exception.detail)
        traitement.refresh_from_db()
        self.assertEqual(traitement.statut, Traitement.Statut.EN_COURS)
        self.assertIsNone(traitement.effectif_final)

    def test_suppression_annule_les_entrees_et_refuse_apres_mortalite(self):
        traitement = self._creer_traitement(hapas=self._hapas_data())
        entries = MouvementEffectif.objects.filter(
            operation_source_content_type=ContentType.objects.get_for_model(
                Traitement
            ),
            operation_source_object_id=traitement.pk,
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
        )

        supprimer_traitement(traitement.pk)

        self.assertFalse(Traitement.objects.filter(pk=traitement.pk).exists())
        self.assertEqual(
            Lot.objects.get(pk=traitement.lot_id).stade,
            Lot.Stade.CREEE,
        )
        self.assertTrue(
            all(entry.annulations_associees.exists() for entry in entries)
        )

        traitement_suivant = self._creer_traitement(
            hapas=self._hapas_data(),
        )
        enregistrer_evenement_mortalite_perte(
            bassin=self.bassin,
            hapa=self.hapa1,
            espece=Lot.Espece.CARPE,
            type_evenement=EvenementMortalitePerte.TypeEvenement.MORTALITE,
            quantite=1,
            date_evenement=self.today,
            saisi_par=self.user,
            cause_mortalite=CauseMortalite.objects.create(
                libelle="Cause suppression"
            ),
        )
        with self.assertRaises(ValidationError):
            supprimer_traitement(traitement_suivant.pk)

    def test_repli_sans_hapa_et_refus_si_traitement_concurrent_meme_espece(self):
        bassin_sans_hapa = self._bassin("TRAITEMENT-REPLI")
        traitement_sans_hapa = self._creer_traitement(
            bassin=bassin_sans_hapa,
        )
        self.assertFalse(
            TraitementHapa.objects.filter(
                traitement=traitement_sans_hapa
            ).exists()
        )
        self.assertEqual(
            cloturer_traitement(traitement_sans_hapa.pk).effectif_final,
            10,
        )

        bassin_partage = self._bassin("TRAITEMENT-PARTAGE")
        premier = self._creer_traitement(bassin=bassin_partage)
        self._creer_traitement(bassin=bassin_partage)
        with self.assertRaises(ValidationError) as error:
            cloturer_traitement(premier.pk)
        self.assertIn("effectif_final", error.exception.detail)
