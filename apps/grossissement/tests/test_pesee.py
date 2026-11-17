"""Pesée de suivi : une pesée par lot de grossissement et par date."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Role
from apps.effectifs.calculs import effectif_vivant
from apps.effectifs.models import MouvementEffectif
from apps.effectifs.services import enregistrer_mouvement
from apps.grossissement.models import IndicateurLot, LotGrossissement, Pesee
from apps.grossissement.services import _effectifs_a_la_date
from apps.lots.models import Lot

from .test_lot_grossissement import URL_LOTS, OutilsLotGrossissement


User = get_user_model()

URL_PESEES = "/api/v1/grossissement/pesees/"
URL_APERCU_PESEE = f"{URL_PESEES}apercu/"
EFFECTIF = 100
AGE_LOT = 40


class PeseeTests(OutilsLotGrossissement, TestCase):
    def setUp(self):
        super().setUp()
        # Lot mis en place il y a 40 jours avec 100 poissons (cible 10 % = 10).
        _, _, sortie_id, _ = self._sortie_validee(
            "LOT-PESEE",
            EFFECTIF,
            il_y_a_jours=AGE_LOT,
        )
        self.gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)

    def _jour(self, il_y_a_jours):
        return (self.today - timedelta(days=il_y_a_jours)).isoformat()

    def _peser(self, il_y_a_jours, nombre=10, poids_total="500", client=None, **extra):
        donnees = {
            "id_lot_grossissement": self.gross.pk,
            "date": self._jour(il_y_a_jours),
            "nombre_poissons_peses": nombre,
            "poids_total_g": poids_total,
            **extra,
        }
        return (client or self.client).post(URL_PESEES, donnees, format="json")

    def _creer(self, il_y_a_jours, **kwargs):
        reponse = self._peser(il_y_a_jours, **kwargs)
        self.assertEqual(reponse.status_code, 201, reponse.data)
        return reponse.data

    def _modifier(self, pesee_id, donnees, client=None):
        return (client or self.client).patch(
            f"{URL_PESEES}{pesee_id}/", donnees, format="json"
        )

    def _annuler_pesee(self, pesee_id, motif="Erreur de saisie", client=None):
        return (client or self.client).post(
            f"{URL_PESEES}{pesee_id}/annuler/", {"motif": motif}, format="json"
        )

    def _fiche_lot(self):
        reponse = self.client.get(f"{URL_LOTS}{self.gross.pk}/")
        self.assertEqual(reponse.status_code, 200, reponse.data)
        return reponse.data

    def _client_employe(self):
        employe = User.objects.create_user(
            username="pesee-employe",
            password="test-password",
            role=Role.objects.get_or_create(libelle="Employé")[0],
            statut="ACTIF",
        )
        client = APIClient()
        client.force_authenticate(employe)
        return client

    # --- Création -------------------------------------------------------

    def test_creation_renseigne_les_valeurs_automatiques_et_calculees(self):
        data = self._creer(10, poids_total="512.50", observation="Pesée J30")

        self.assertEqual(data["id_lot_grossissement"], self.gross.pk)
        self.assertEqual(data["espece"], Lot.Espece.CARPE)
        self.assertEqual(data["id_bassin"], self.bassin_destination.pk)
        self.assertEqual(data["id_user"], self.user.pk)
        self.assertEqual(data["effectif_vivant"], EFFECTIF)
        self.assertEqual(data["pourcentage_echantillon"], "10.00")
        self.assertEqual(data["taille_echantillon_cible"], 10)
        self.assertEqual(data["pourcentage_reel"], "10.00")
        self.assertEqual(data["poids_moyen"], "51.25")
        self.assertTrue(data["echantillon_conforme"])
        self.assertEqual(data["statut"], Pesee.Statut.VALIDEE)
        self.assertIsNotNone(data["created_at"])
        self.assertIsNotNone(data["updated_at"])
        # 100 poissons × 51,25 g = 5,125 kg ; GMQ cumulé (51,25 − 20) / 30 j.
        self.assertEqual(data["indicateurs"]["biomasse_kg"], "5.125")
        self.assertEqual(data["indicateurs"]["gmq_cumule"], "1.04")
        self.assertEqual(data["indicateurs"]["jour_de_cycle"], 30)

        fiche = self._fiche_lot()
        self.assertEqual(fiche["poids_moyen_actuel"], "51.25")
        self.assertEqual(fiche["date_derniere_pesee"], self._jour(10))
        self.assertEqual(fiche["derniers_indicateurs"]["biomasse_kg"], "5.125")

    def test_fiche_sans_pesee_expose_le_poids_initial(self):
        fiche = self._fiche_lot()
        self.assertEqual(fiche["poids_moyen_actuel"], "20.00")
        self.assertIsNone(fiche["date_derniere_pesee"])
        self.assertIsNone(fiche["derniers_indicateurs"])

    def test_champs_automatiques_refuses(self):
        for champ, valeur in (
            ("id_bassin", self.bassin_destination.pk),
            ("effectif_vivant", 50),
            ("poids_moyen", "10"),
            ("pourcentage_reel", "10"),
            ("lignes", []),
        ):
            with self.subTest(champ=champ):
                reponse = self._peser(10, **{champ: valeur})
                self.assertEqual(reponse.status_code, 400, reponse.data)
                self.assertIn(champ, reponse.data)
        self.assertFalse(Pesee.objects.exists())

    def test_champs_obligatoires(self):
        for champ in ("id_lot_grossissement", "date", "nombre_poissons_peses", "poids_total_g"):
            with self.subTest(champ=champ):
                donnees = {
                    "id_lot_grossissement": self.gross.pk,
                    "date": self._jour(10),
                    "nombre_poissons_peses": 10,
                    "poids_total_g": "500",
                }
                donnees.pop(champ)
                reponse = self.client.post(URL_PESEES, donnees, format="json")
                self.assertEqual(reponse.status_code, 400, reponse.data)
                self.assertIn(champ, reponse.data)

    def test_date_future_ou_avant_mise_en_place_refusee(self):
        for il_y_a_jours in (-1, AGE_LOT + 1):
            with self.subTest(il_y_a_jours=il_y_a_jours):
                reponse = self._peser(il_y_a_jours)
                self.assertEqual(reponse.status_code, 400, reponse.data)
                self.assertIn("date", reponse.data)
        self.assertFalse(Pesee.objects.exists())

    def test_date_libre_dans_le_cycle_mais_apres_la_derniere_pesee(self):
        # J7 n'est pas une échéance du calendrier : la pesée est acceptée.
        self._creer(AGE_LOT - 7)
        for il_y_a_jours in (AGE_LOT - 7, AGE_LOT - 2):
            with self.subTest(il_y_a_jours=il_y_a_jours):
                reponse = self._peser(il_y_a_jours)
                self.assertEqual(reponse.status_code, 400, reponse.data)
                self.assertIn("date", reponse.data)
        self._creer(0)
        self.assertEqual(Pesee.objects.count(), 2)

    def test_echantillon_non_conforme_exige_une_confirmation(self):
        refusee = self._peser(10, nombre=5, poids_total="250")
        self.assertEqual(refusee.status_code, 400, refusee.data)
        self.assertIn("echantillon_non_conforme", refusee.data)
        self.assertEqual(
            refusee.data["lignes_non_conformes"][0]["taille_echantillon_cible"], 10
        )
        self.assertFalse(Pesee.objects.exists())

        data = self._creer(
            10, nombre=5, poids_total="250", confirmer_echantillon_non_conforme=True
        )
        self.assertFalse(data["echantillon_conforme"])
        self.assertEqual(data["pourcentage_reel"], "5.00")

    def test_nombre_superieur_a_l_effectif_ou_mesures_nulles_refuses(self):
        for nombre, poids, champ in (
            (EFFECTIF + 1, "500", "nombre_poissons_peses"),
            (0, "500", "nombre_poissons_peses"),
            (10, "0", "poids_total_g"),
        ):
            with self.subTest(nombre=nombre, poids=poids):
                reponse = self._peser(10, nombre=nombre, poids_total=poids)
                self.assertEqual(reponse.status_code, 400, reponse.data)
                self.assertIn(champ, reponse.data)

    def test_lot_cloture_ne_peut_pas_etre_pese(self):
        self._cloturer(self.gross, evacuer=False)
        reponse = self._peser(10)
        self.assertEqual(reponse.status_code, 400, reponse.data)
        self.assertIn("id_lot_grossissement", reponse.data)

    # --- Aperçu ---------------------------------------------------------

    def test_apercu_preremplit_le_formulaire_sans_rien_ecrire(self):
        reponse = self.client.post(
            URL_APERCU_PESEE,
            {"id_lot_grossissement": self.gross.pk, "date": self._jour(10)},
            format="json",
        )
        self.assertEqual(reponse.status_code, 200, reponse.data)
        self.assertEqual(reponse.data["espece"], Lot.Espece.CARPE)
        self.assertEqual(reponse.data["id_bassin"], self.bassin_destination.pk)
        self.assertEqual(reponse.data["effectif_vivant"], EFFECTIF)
        self.assertEqual(reponse.data["taille_echantillon_cible"], 10)
        self.assertEqual(reponse.data["date_min"], self._jour(AGE_LOT))
        self.assertIsNone(reponse.data["poids_moyen"])
        self.assertIsNone(reponse.data["pourcentage_reel"])
        self.assertIsNone(reponse.data["echantillon_conforme"])

        complet = self.client.post(
            URL_APERCU_PESEE,
            {
                "id_lot_grossissement": self.gross.pk,
                "date": self._jour(10),
                "nombre_poissons_peses": 8,
                "poids_total_g": "400",
            },
            format="json",
        )
        self.assertEqual(complet.status_code, 200, complet.data)
        self.assertEqual(complet.data["poids_moyen"], "50.00")
        self.assertEqual(complet.data["pourcentage_reel"], "8.00")
        self.assertFalse(complet.data["echantillon_conforme"])
        self.assertFalse(Pesee.objects.exists())

        self._creer(10)
        apres = self.client.post(
            URL_APERCU_PESEE, {"id_lot_grossissement": self.gross.pk}, format="json"
        )
        self.assertEqual(apres.status_code, 200, apres.data)
        self.assertEqual(apres.data["date"], self.today.isoformat())
        self.assertEqual(apres.data["date_derniere_pesee"], self._jour(10))
        self.assertEqual(apres.data["date_min"], self._jour(9))

    # --- Correction et annulation ---------------------------------------

    def test_seule_la_derniere_pesee_est_corrigeable_et_la_date_est_figee(self):
        premiere = self._creer(20)
        derniere = self._creer(10)

        refusee = self._modifier(premiere["id"], {"poids_total_g": "300"})
        self.assertEqual(refusee.status_code, 400, refusee.data)
        self.assertIn("detail", refusee.data)

        for champ, valeur in (
            ("date", self._jour(5)),
            ("id_lot_grossissement", self.gross.pk),
        ):
            with self.subTest(champ=champ):
                reponse = self._modifier(derniere["id"], {champ: valeur})
                self.assertEqual(reponse.status_code, 400, reponse.data)
                self.assertIn(champ, reponse.data)

        corrigee = self._modifier(
            derniere["id"], {"poids_total_g": "600", "observation": "Corrigée"}
        )
        self.assertEqual(corrigee.status_code, 200, corrigee.data)
        self.assertEqual(corrigee.data["poids_moyen"], "60.00")
        self.assertEqual(corrigee.data["date"], self._jour(10))
        self.assertEqual(corrigee.data["observation"], "Corrigée")
        self.assertEqual(corrigee.data["indicateurs"]["biomasse_kg"], "6.000")
        self.assertEqual(self._fiche_lot()["poids_moyen_actuel"], "60.00")

    def test_annulation_motivee_de_la_derniere_pesee(self):
        premiere = self._creer(20)
        derniere = self._creer(10)

        self.assertEqual(self._annuler_pesee(premiere["id"]).status_code, 400)
        sans_motif = self._annuler_pesee(derniere["id"], motif=" ")
        self.assertEqual(sans_motif.status_code, 400, sans_motif.data)
        self.assertIn("motif", sans_motif.data)

        annulee = self._annuler_pesee(derniere["id"])
        self.assertEqual(annulee.status_code, 200, annulee.data)
        self.assertEqual(annulee.data["statut"], Pesee.Statut.ANNULEE)
        self.assertEqual(annulee.data["motif_annulation"], "Erreur de saisie")
        self.assertEqual(annulee.data["id_user_annulation"], self.user.pk)
        self.assertIsNotNone(annulee.data["annulee_le"])
        self.assertIsNone(annulee.data["indicateurs"])
        self.assertFalse(IndicateurLot.objects.filter(pesee_id=derniere["id"]).exists())
        self.assertTrue(Pesee.objects.filter(pk=derniere["id"]).exists())

        # La pesée précédente redevient la dernière : corrigeable.
        self.assertEqual(self._fiche_lot()["poids_moyen_actuel"], "50.00")
        corrigee = self._modifier(premiere["id"], {"poids_total_g": "450"})
        self.assertEqual(corrigee.status_code, 200, corrigee.data)
        # Une pesée annulée ne se corrige ni ne s'annule plus.
        self.assertEqual(self._modifier(derniere["id"], {"poids_total_g": "1"}).status_code, 400)
        self.assertEqual(self._annuler_pesee(derniere["id"]).status_code, 400)
        # La date de la pesée annulée est libérée.
        self._creer(10)

    def test_suppression_interdite(self):
        pesee = self._creer(10)
        reponse = self.client.delete(f"{URL_PESEES}{pesee['id']}/")
        self.assertEqual(reponse.status_code, 405)
        self.assertTrue(Pesee.objects.filter(pk=pesee["id"]).exists())

    def test_permissions_employe(self):
        employe = self._client_employe()
        creee = self._peser(10, client=employe)
        self.assertEqual(creee.status_code, 201, creee.data)
        apercu = employe.post(
            URL_APERCU_PESEE, {"id_lot_grossissement": self.gross.pk}, format="json"
        )
        self.assertEqual(apercu.status_code, 200, apercu.data)
        self.assertEqual(employe.get(URL_PESEES).status_code, 200)
        self.assertEqual(
            self._modifier(creee.data["id"], {"poids_total_g": "600"}, client=employe).status_code,
            403,
        )
        self.assertEqual(self._annuler_pesee(creee.data["id"], client=employe).status_code, 403)

    def test_filtres(self):
        premiere = self._creer(20)
        derniere = self._creer(10)
        self._annuler_pesee(derniere["id"])
        for params, attendus in (
            ({"lot": self.gross.pk}, {premiere["id"], derniere["id"]}),
            ({"statut": "ANNULEE"}, {derniere["id"]}),
            ({"statut": "VALIDEE", "bassin": self.bassin_destination.pk}, {premiere["id"]}),
            ({"date_min": self._jour(15)}, {derniere["id"]}),
        ):
            with self.subTest(params=params):
                reponse = self.client.get(URL_PESEES, params)
                self.assertEqual(reponse.status_code, 200, reponse.data)
                identifiants = {pesee["id"] for pesee in reponse.data}
                self.assertEqual(identifiants, attendus)
        self.assertEqual(self.client.get(URL_PESEES, {"statut": "X"}).status_code, 400)

    # --- Indicateurs et registre ----------------------------------------

    def test_recalcul_relit_l_effectif_dans_le_registre(self):
        premiere = self._creer(10)
        # Mortalité antérieure saisie après coup : 20 poissons à J-15.
        enregistrer_mouvement(
            bassin=self.bassin_destination,
            espece=Lot.Espece.CARPE,
            type_mouvement=MouvementEffectif.TypeMouvement.MORTALITE,
            quantite=20,
            date_evenement=self.today - timedelta(days=15),
            saisi_par=self.user,
        )
        self._creer(5, nombre=8, poids_total="480")

        pesee = Pesee.objects.get(pk=premiere["id"])
        # La trace garde l'effectif vu lors de la saisie ; l'indicateur relit le registre.
        self.assertEqual(pesee.effectif_vivant, EFFECTIF)
        self.assertEqual(pesee.indicateur.effectif_vivant, EFFECTIF - 20)
        self.assertEqual(pesee.indicateur.biomasse_kg, Decimal("4.000"))

    def test_effectifs_en_memoire_identiques_au_registre(self):
        enregistrer_mouvement(
            bassin=self.bassin_destination,
            espece=Lot.Espece.CARPE,
            type_mouvement=MouvementEffectif.TypeMouvement.MORTALITE,
            quantite=7,
            date_evenement=self.today - timedelta(days=3),
            saisi_par=self.user,
        )
        effectif_a = _effectifs_a_la_date(
            self.bassin_destination.pk, [self.gross], self.today
        )
        for il_y_a_jours in (AGE_LOT + 1, AGE_LOT, 4, 3, 0):
            jour = self.today - timedelta(days=il_y_a_jours)
            with self.subTest(jour=jour):
                self.assertEqual(
                    effectif_a(self.gross.pk, jour),
                    effectif_vivant(
                        self.bassin_destination.pk, Lot.Espece.CARPE, a_la_date=jour
                    ),
                )

    def test_calendrier_indicatif_signale_la_pesee_en_retard(self):
        # Mise en place à J-40 : l'échéance J30 (J-10) est passée sans pesée.
        fiche = self._fiche_lot()
        self.assertTrue(fiche["pesee_en_retard"])
        self.assertEqual(fiche["etape_courante"], 2)
        self.assertEqual(fiche["prochaine_echeance_pesee"], self._jour(10))
        self.assertEqual(fiche["jour_de_cycle"], AGE_LOT)
        self.assertEqual(fiche["date_entree"], self._jour(AGE_LOT))

        # Pesée faite à l'échéance J30 : la prochaine est J60 (J+20).
        self._creer(10)
        fiche = self._fiche_lot()
        self.assertFalse(fiche["pesee_en_retard"])
        self.assertEqual(fiche["prochaine_echeance_pesee"], self._jour(-20))

    def test_une_pesee_meme_annulee_empeche_d_annuler_la_sortie(self):
        pesee = self._creer(10)
        self._annuler_pesee(pesee["id"])
        reponse = self._annuler(self.gross.sortie_stock_id)
        self.assertEqual(reponse.status_code, 400, reponse.data)
        self.assertTrue(LotGrossissement.objects.filter(pk=self.gross.pk).exists())
