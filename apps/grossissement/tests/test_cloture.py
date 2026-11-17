"""Clôture d'un lot de grossissement : seuil, résumé, évacuation et historique."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.bassins.models import Bassin
from apps.core.models import Role
from apps.effectifs.calculs import effectif_vivant
from apps.effectifs.models import MouvementEffectif
from apps.effectifs.services import enregistrer_mouvement
from apps.grossissement.models import LotGrossissement
from apps.grossissement.services import creer_pesee
from apps.lots.models import Lot
from apps.provende.models import ConsommationProvende, Provende, TypeProvende
from apps.provende.services import annuler_distribution, enregistrer_distribution

from .test_lot_grossissement import URL_LOTS, OutilsLotGrossissement


User = get_user_model()

EFFECTIF = 100
AGE_LOT = 40


class ClotureTests(OutilsLotGrossissement, TestCase):
    def setUp(self):
        super().setUp()
        # Lot CARPE (seuil 300 g) mis en place il y a 40 jours avec 100 poissons de 20 g.
        _, _, sortie_id, _ = self._sortie_validee("LOT-CLOT", EFFECTIF, il_y_a_jours=AGE_LOT)
        self.gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)

    # --- Outils -----------------------------------------------------------

    def _jour(self, il_y_a_jours):
        return self.today - timedelta(days=il_y_a_jours)

    def _peser(self, poids_moyen, il_y_a_jours=0, lot=None, nombre=None):
        lot = lot or self.gross
        jour = self._jour(il_y_a_jours)
        effectif = effectif_vivant(lot.bassin_id, lot.espece, a_la_date=jour)
        nombre = nombre or max(1, -(-effectif // 10))
        return creer_pesee(
            lot_id=lot.pk,
            date_pesee=jour,
            nombre=nombre,
            poids_total=Decimal(poids_moyen) * nombre,
            utilisateur=self.user,
        )

    def _mortalite(self, quantite, il_y_a_jours=0, bassin=None, espece=Lot.Espece.CARPE):
        return enregistrer_mouvement(
            bassin=bassin or self.bassin_destination,
            espece=espece,
            type_mouvement=MouvementEffectif.TypeMouvement.MORTALITE,
            quantite=quantite,
            date_evenement=self._jour(il_y_a_jours),
            saisi_par=self.user,
        )

    def _url(self, action, lot=None):
        return f"{URL_LOTS}{(lot or self.gross).pk}/{action}/"

    def _apercu(self, lot=None, client=None):
        reponse = (client or self.client).get(self._url("apercu-cloture", lot))
        self.assertEqual(reponse.status_code, 200, reponse.data)
        return reponse.json()

    def _cloturer_api(self, donnees=None, lot=None, client=None):
        return (client or self.client).post(
            self._url("cloturer", lot), donnees or {}, format="json"
        )

    def _evacuer_api(self, lot=None, client=None):
        return (client or self.client).post(self._url("evacuer", lot), {}, format="json")

    def _lot_tilapia(self, reference="LOT-TILA", destination=None):
        lot, _ = self._mettre_en_stock(
            reference,
            EFFECTIF,
            il_y_a_jours=AGE_LOT + 1,
            espece=Lot.Espece.TILAPIA,
            hapa=self._hapa(self.bassin_stock, f"STOCK-{reference}"),
        )
        destination = destination or self._bassin(
            f"GROSS-{reference}", Bassin.Categorie.GROSSISSEMENT
        )
        validee = self._valider(self._donnees_sortie(
            lot, EFFECTIF, il_y_a_jours=AGE_LOT, destination=destination
        ))
        self.assertEqual(validee.status_code, 201, validee.data)
        return LotGrossissement.objects.get(pk=validee.data["id"])

    def _client_employe(self):
        employe = User.objects.create_user(
            username="cloture-employe",
            password="test-password",
            role=Role.objects.create(libelle="Employé"),
            statut="ACTIF",
        )
        client = APIClient()
        client.force_authenticate(employe)
        return client

    # --- Aperçu -----------------------------------------------------------

    def test_apercu_resume_la_cloture_sans_rien_ecrire(self):
        self._peser("300")
        data = self._apercu()
        self.assertEqual(data, {
            "id_lot_grossissement": self.gross.pk,
            "code": self.gross.code,
            "espece": "CARPE",
            "statut": "ACTIF",
            "date_cloture": self.today.isoformat(),
            "effectif_initial": EFFECTIF,
            "effectif_final": EFFECTIF,
            "poids_moyen_initial": 20.0,
            "poids_moyen_final": 300.0,
            "date_derniere_pesee": self.today.isoformat(),
            "seuil_poids_vente_g": 300,
            "ecart_poids_pourcentage": 1400.0,
            "cloturable": True,
            "raison_refus": None,
        })
        self.gross.refresh_from_db()
        self.assertEqual(self.gross.statut, LotGrossissement.Statut.ACTIF)

    def test_cloture_refusee_sans_pesee(self):
        data = self._apercu()
        self.assertFalse(data["cloturable"])
        self.assertIn("Aucune pesée", data["raison_refus"])
        self.assertIsNone(data["poids_moyen_final"])
        reponse = self._cloturer_api()
        self.assertEqual(reponse.status_code, 400, reponse.data)
        self.assertIn("poids_moyen_final", reponse.data)

    def test_seuil_carpe_300_g(self):
        self._peser("299.99", il_y_a_jours=1)
        data = self._apercu()
        self.assertFalse(data["cloturable"])
        self.assertIn("300 g", data["raison_refus"])
        refusee = self._cloturer_api()
        self.assertEqual(refusee.status_code, 400, refusee.data)
        self.assertIn("poids_moyen_final", refusee.data)
        self.gross.refresh_from_db()
        self.assertEqual(self.gross.statut, LotGrossissement.Statut.ACTIF)

        self._peser("300.00")
        self.assertEqual(self._cloturer_api().status_code, 200)

    def test_seuil_tilapia_250_g(self):
        tilapia = self._lot_tilapia()
        self._peser("249.99", il_y_a_jours=1, lot=tilapia)
        self.assertFalse(self._apercu(tilapia)["cloturable"])
        self._peser("250.00", lot=tilapia)
        data = self._apercu(tilapia)
        self.assertEqual((data["cloturable"], data["seuil_poids_vente_g"]), (True, 250))
        self.assertEqual(self._cloturer_api(lot=tilapia).status_code, 200)

    def test_seuil_juge_sur_la_derniere_pesee(self):
        self._peser("310", il_y_a_jours=10)
        self._peser("290")
        data = self._apercu()
        self.assertFalse(data["cloturable"])
        self.assertEqual(data["poids_moyen_final"], 290.0)

    # --- Clôture ----------------------------------------------------------

    def test_cloture_enregistre_les_finaux_sans_mouvement(self):
        self._mortalite(5, il_y_a_jours=5)
        self._peser("305.50")
        mouvements = MouvementEffectif.objects.count()

        reponse = self._cloturer_api({"observation": "Fin du cycle"})

        self.assertEqual(reponse.status_code, 200, reponse.data)
        data = reponse.data
        self.assertEqual(data["statut"], LotGrossissement.Statut.CLOTURE)
        self.assertEqual(data["date_cloture"], self.today.isoformat())
        self.assertEqual(data["effectif_final"], EFFECTIF - 5)
        self.assertEqual(data["poids_moyen_final"], "305.50")
        self.assertEqual(data["motif_cloture"], "Fin du cycle")
        self.assertTrue(data["evacuation_requise"])
        self.assertIsNone(data["evacue_le"])
        self.assertEqual(data["message"], f"Le cycle du lot {self.gross.code} a bien été clôturé.")
        self.assertEqual(
            Lot.objects.get(pk=self.gross.lot_origine_id).stade, Lot.Stade.CLOTUREE
        )
        # La clôture n'écrit aucun mouvement : l'effectif reste dans le registre.
        self.assertEqual(MouvementEffectif.objects.count(), mouvements)
        self.assertEqual(
            effectif_vivant(self.bassin_destination.pk, Lot.Espece.CARPE), EFFECTIF - 5
        )

    def test_observation_facultative(self):
        self._peser("300")
        reponse = self._cloturer_api()
        self.assertEqual(reponse.status_code, 200, reponse.data)
        self.assertEqual(reponse.data["motif_cloture"], "")

    def test_ancien_format_refuse(self):
        self._peser("300")
        for champ, valeur in (
            ("motif", "Fin du cycle"),
            ("poids_final_g", "300"),
            ("poids_moyen_final", "300"),
            ("date_cloture", self.today.isoformat()),
            ("effectif_final", 10),
        ):
            with self.subTest(champ=champ):
                reponse = self._cloturer_api({champ: valeur})
                self.assertEqual(reponse.status_code, 400, reponse.data)
                self.assertIn(champ, reponse.data)
        self.gross.refresh_from_db()
        self.assertEqual(self.gross.statut, LotGrossissement.Statut.ACTIF)

    def test_double_cloture_refusee(self):
        self._peser("300")
        self.assertEqual(self._cloturer_api().status_code, 200)
        reponse = self._cloturer_api()
        self.assertEqual(reponse.status_code, 400, reponse.data)
        self.assertIn("statut", reponse.data)
        self.assertFalse(self._apercu()["cloturable"])

    def test_cloture_atomique(self):
        self._peser("300")
        with patch.object(Lot, "save", side_effect=RuntimeError("panne")):
            with self.assertRaises(RuntimeError):
                self._cloturer_api()
        self.gross.refresh_from_db()
        self.assertEqual(self.gross.statut, LotGrossissement.Statut.ACTIF)
        self.assertIsNone(self.gross.date_cloture)

    def test_lot_cloture_non_modifiable(self):
        self._peser("300")
        self._cloturer_api()
        reponse = self.client.patch(
            f"{URL_LOTS}{self.gross.pk}/", {"observation": "Modif"}, format="json"
        )
        self.assertEqual(reponse.status_code, 400, reponse.data)
        pesee = self.client.post(
            "/api/v1/grossissement/pesees/",
            {
                "id_lot_grossissement": self.gross.pk,
                "date": self.today.isoformat(),
                "nombre_poissons_peses": 10,
                "poids_total_g": "3100",
            },
            format="json",
        )
        self.assertEqual(pesee.status_code, 400, pesee.data)

    # --- Évacuation -------------------------------------------------------

    def test_evacuation_differee_sortie_datee_de_la_cloture(self):
        self._mortalite(5, il_y_a_jours=5)
        self._peser("300")
        self._cloturer_api()

        reponse = self._evacuer_api()

        self.assertEqual(reponse.status_code, 200, reponse.data)
        self.assertIsNotNone(reponse.data["evacue_le"])
        self.assertFalse(reponse.data["evacuation_requise"])
        self.assertEqual(
            reponse.data["message"], f"Le bassin du lot {self.gross.code} a bien été évacué."
        )
        sortie = MouvementEffectif.objects.get(
            bassin=self.bassin_destination,
            type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
        )
        self.assertEqual(sortie.quantite, EFFECTIF - 5)
        self.assertEqual(sortie.date_evenement, self.today)
        self.assertEqual(
            sortie.operation_source_content_type,
            ContentType.objects.get_for_model(LotGrossissement),
        )
        self.assertEqual(sortie.operation_source_object_id, self.gross.pk)
        self.assertEqual(effectif_vivant(self.bassin_destination.pk, Lot.Espece.CARPE), 0)

        deuxieme = self._evacuer_api()
        self.assertEqual(deuxieme.status_code, 400, deuxieme.data)
        self.assertIn("detail", deuxieme.data)

    def test_evacuation_refusee_pour_un_lot_actif(self):
        reponse = self._evacuer_api()
        self.assertEqual(reponse.status_code, 400, reponse.data)
        self.assertIn("statut", reponse.data)

    def test_effectif_nul_rien_a_evacuer(self):
        self._peser("300", il_y_a_jours=2)
        self._mortalite(EFFECTIF, il_y_a_jours=1)
        reponse = self._cloturer_api()
        self.assertEqual(reponse.status_code, 200, reponse.data)
        self.assertEqual(reponse.data["effectif_final"], 0)
        self.assertFalse(reponse.data["evacuation_requise"])
        evacuation = self._evacuer_api()
        self.assertEqual(evacuation.status_code, 400, evacuation.data)

    def test_registre_fige_apres_la_cloture(self):
        self._peser("300")
        self._cloturer_api()
        # Avant l'évacuation : aucune saisie, ni à la date de clôture ni rétroactive.
        for il_y_a_jours in (0, 3):
            with self.subTest(il_y_a_jours=il_y_a_jours):
                with self.assertRaises(ValidationError) as ctx:
                    self._mortalite(1, il_y_a_jours=il_y_a_jours)
                self.assertIn("clôturé", str(ctx.exception.detail))
        # Après l'évacuation, la période du lot reste figée.
        self._evacuer_api()
        with self.assertRaises(ValidationError):
            self._mortalite(1, il_y_a_jours=3)
        self.assertEqual(effectif_vivant(self.bassin_destination.pk, Lot.Espece.CARPE), 0)

    def test_saisie_d_une_autre_espece_du_bassin_non_bloquee(self):
        # Bassin + espèce identifient le lot : le TILAPIA voisin reste saisissable.
        self._lot_tilapia(destination=self.bassin_destination)
        self._peser("300")
        self._cloturer_api()
        self._mortalite(1, espece=Lot.Espece.TILAPIA)
        self.assertEqual(
            effectif_vivant(self.bassin_destination.pk, Lot.Espece.TILAPIA), EFFECTIF - 1
        )

    def test_nouveau_lot_meme_espece_attend_l_evacuation(self):
        self._peser("300")
        self._cloturer_api()
        lot, _ = self._mettre_en_stock(
            "LOT-SUIVANT", 10, hapa=self._hapa(self.bassin_stock, "STOCK-SUIVANT")
        )
        refusee = self._valider(self._donnees_sortie(lot, 10))
        self.assertEqual(refusee.status_code, 400, refusee.data)
        self.assertIn("id_bassin_destination", refusee.data)
        self.assertIn("évacué", str(refusee.data["id_bassin_destination"]))

        self.assertEqual(self._evacuer_api().status_code, 200)
        acceptee = self._valider(self._donnees_sortie(lot, 10))
        self.assertEqual(acceptee.status_code, 201, acceptee.data)

    # --- Provende : chiffres d'un lot clôturé figés -------------------------

    def _distribuer(self, il_y_a_jours, bassin=None, quantite="5"):
        type_provende, _ = TypeProvende.objects.get_or_create(
            type=TypeProvende.Type.CROISSANCE
        )
        provende, _ = Provende.objects.get_or_create(
            reference="PROV-CLOTURE",
            defaults={
                "type_provende": type_provende,
                "date_creation": self._jour(AGE_LOT + 5),
            },
        )
        return enregistrer_distribution(
            {
                "bassin": bassin or self.bassin_destination,
                "provende": provende,
                "date_distribution": timezone.make_aware(
                    datetime.combine(self._jour(il_y_a_jours), time(8, 0)),
                    timezone.get_current_timezone(),
                ),
                "quantite_kg": Decimal(quantite),
            },
            self.user,
        )

    def _reculer_cloture(self, jours):
        """Simule une clôture faite il y a `jours` jours (la clôture est datée du jour)."""
        LotGrossissement.objects.filter(pk=self.gross.pk).update(
            date_cloture=self._jour(jours)
        )

    def test_provende_refusee_sur_la_periode_d_un_lot_cloture(self):
        self._distribuer(10)
        self._peser("300")
        self._cloturer_api()
        for il_y_a_jours in (0, 10):
            with self.subTest(il_y_a_jours=il_y_a_jours):
                with self.assertRaises(ValidationError) as ctx:
                    self._distribuer(il_y_a_jours)
                self.assertIn("date_distribution", ctx.exception.detail)
                self.assertIn("clôturé", str(ctx.exception.detail))
        self.assertEqual(ConsommationProvende.objects.count(), 1)

    def test_provende_refusee_sans_lot_en_cours(self):
        # Lot clôturé il y a 5 jours, bassin non évacué : poissons présents,
        # mais aucun lot en cours pour recevoir la provende.
        self._peser("300", il_y_a_jours=6)
        self._cloturer_api()
        self._reculer_cloture(5)
        with self.assertRaises(ValidationError) as ctx:
            self._distribuer(2)
        self.assertIn("id_bassin", ctx.exception.detail)

    def test_provende_acceptee_pour_l_autre_espece_en_cours(self):
        self._lot_tilapia(destination=self.bassin_destination)
        self._peser("300", il_y_a_jours=6)
        self._cloturer_api()
        self._reculer_cloture(5)
        # Après la clôture de la CARPE, le TILAPIA en cours reçoit la provende...
        self.assertIsNotNone(self._distribuer(2))
        # ... mais la période de la CARPE clôturée reste figée.
        with self.assertRaises(ValidationError):
            self._distribuer(6)

    def test_annulation_de_provende_refusee_sur_la_periode_close(self):
        avant = self._distribuer(10)
        hors_periode = self._distribuer(20)
        annuler_distribution(hors_periode.pk, "Erreur avant clôture", self.user)
        self._peser("300")
        self._cloturer_api()
        with self.assertRaises(ValidationError) as ctx:
            annuler_distribution(avant.pk, "Erreur de saisie", self.user)
        self.assertIn("clôturé", str(ctx.exception.detail))
        avant.refresh_from_db()
        self.assertEqual(avant.statut, ConsommationProvende.Statut.VALIDE)

    # --- Historique et permissions ----------------------------------------

    def test_historique_des_clotures(self):
        self._mortalite(5, il_y_a_jours=5)
        self._peser("300")
        self._cloturer_api()
        self._evacuer_api()
        tilapia = self._lot_tilapia()
        self._peser("260", lot=tilapia)
        self._cloturer_api(lot=tilapia)
        self._lot_tilapia("LOT-ACTIF")  # en cours : absent de l'historique

        reponse = self.client.get(f"{URL_LOTS}historique-clotures/")
        self.assertEqual(reponse.status_code, 200, reponse.data)
        par_code = {ligne["code"]: ligne for ligne in reponse.data}
        self.assertEqual(set(par_code), {self.gross.code, tilapia.code})
        carpe = par_code[self.gross.code]
        self.assertEqual(
            {cle: carpe[cle] for cle in carpe if cle not in {"id", "evacue_le"}},
            {
                "code": self.gross.code,
                "espece": "CARPE",
                "id_bassin": self.bassin_destination.pk,
                "reference_bassin": "GROSS-DEST",
                "date_entree": self._jour(AGE_LOT).isoformat(),
                "date_cloture": self.today.isoformat(),
                "duree_cycle_jours": AGE_LOT,
                "effectif_initial": EFFECTIF,
                "effectif_final": EFFECTIF - 5,
                "taux_survie": "95.00",
                "poids_moyen_initial": "20.00",
                "poids_moyen_final": "300.00",
                "ecart_poids_pourcentage": "1400.00",
                "evacue": True,
            },
        )
        self.assertIsNotNone(carpe["evacue_le"])
        self.assertFalse(par_code[tilapia.code]["evacue"])
        self.assertEqual(par_code[tilapia.code]["ecart_poids_pourcentage"], "1200.00")

        for params, attendus in (
            ({"espece": "TILAPIA"}, {tilapia.code}),
            ({"bassin": self.bassin_destination.pk}, {self.gross.code}),
            ({"date_min": (self.today + timedelta(days=1)).isoformat()}, set()),
            ({"date_max": self.today.isoformat()}, {self.gross.code, tilapia.code}),
        ):
            with self.subTest(params=params):
                filtre = self.client.get(f"{URL_LOTS}historique-clotures/", params)
                self.assertEqual(filtre.status_code, 200, filtre.data)
                self.assertEqual({ligne["code"] for ligne in filtre.data}, attendus)
        for params in ({"espece": "X"}, {"bassin": "a"}, {"date_min": "2026-02-30"}):
            with self.subTest(params=params):
                self.assertEqual(
                    self.client.get(f"{URL_LOTS}historique-clotures/", params).status_code,
                    400,
                )

    def test_permissions(self):
        self._peser("300")
        employe = self._client_employe()
        self.assertEqual(employe.get(self._url("apercu-cloture")).status_code, 200)
        self.assertEqual(employe.get(f"{URL_LOTS}historique-clotures/").status_code, 200)
        self.assertEqual(self._cloturer_api(client=employe).status_code, 403)
        self._cloturer_api()
        self.assertEqual(self._evacuer_api(client=employe).status_code, 403)
        self.assertEqual(
            APIClient().get(self._url("apercu-cloture")).status_code, 401
        )
