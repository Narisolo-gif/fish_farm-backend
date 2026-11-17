"""Clôture d'un traitement : boîte de validation (aperçu) et droits."""
from datetime import timedelta
from rest_framework.test import APIClient

from apps.core.models import Role
from apps.effectifs.models import CauseMortalite, EvenementMortalitePerte
from apps.effectifs.services import hapa_est_bloque
from apps.effectifs.tests.outils import enregistrer_mortalite_perte
from apps.lots.models import Lot
from apps.traitement.models import Traitement

from .test_hapas import TraitementHapaTests, User


URL = "/api/v1/traitement/"


class ClotureTraitementTests(TraitementHapaTests):
    """Réutilise les assistants de TraitementHapaTests (voir la fin du module)."""

    def setUp(self):
        super().setUp()
        self.traitement = self._creer_traitement(hapas=self._hapas_data())
        enregistrer_mortalite_perte(
            bassin=self.bassin,
            hapa=self.hapa1,
            espece=Lot.Espece.CARPE,
            type_evenement=EvenementMortalitePerte.TypeEvenement.MORTALITE,
            quantite=2,
            date_evenement=self.today,
            saisi_par=self.user,
            cause_mortalite=CauseMortalite.objects.create(libelle="Cause clôture"),
        )

    def _client(self, libelle):
        utilisateur = User.objects.create_user(
            username=f"cloture-{libelle}",
            password="test-password",
            role=Role.objects.get_or_create(libelle=libelle)[0],
            statut="ACTIF",
        )
        client = APIClient()
        client.force_authenticate(utilisateur)
        return client

    def test_apercu_resume_la_cloture_sans_rien_ecrire(self):
        reponse = self._client("Employé").get(f"{URL}{self.traitement.pk}/apercu-cloture/")

        self.assertEqual(reponse.status_code, 200, reponse.data)
        lot = self.traitement.lot
        self.assertEqual(reponse.data, {
            "id_traitement": self.traitement.pk,
            "id_lot": lot.pk,
            "reference_lot": lot.reference,
            "espece": "CARPE",
            "id_bassin": self.bassin.pk,
            "reference_bassin": self.bassin.reference,
            "statut": "EN_COURS",
            "date_ouverture": self.today.isoformat(),
            "date_cloture": self.today.isoformat(),
            "duree_jours": 0,
            "effectif_initial": 10,
            "effectif_final": 8,
            "morts_et_pertes": 2,
            "taux_survie": "80.00",
            "cloturable": True,
            "raison_refus": None,
        })
        self.traitement.refresh_from_db()
        self.assertEqual(self.traitement.statut, Traitement.Statut.EN_COURS)
        self.assertFalse(hapa_est_bloque(self.hapa1.pk))

    def test_cloture_reservee_au_pilotage(self):
        for libelle in ("Employé", "Gestionnaire"):
            refusee = self._client(libelle).post(f"{URL}{self.traitement.pk}/cloturer/")
            self.assertEqual(refusee.status_code, 403, libelle)
        self.traitement.refresh_from_db()
        self.assertEqual(self.traitement.statut, Traitement.Statut.EN_COURS)

        reponse = self._client("Directeur").post(f"{URL}{self.traitement.pk}/cloturer/")

        self.assertEqual(reponse.status_code, 200, reponse.data)
        self.assertEqual(reponse.data["statut"], Traitement.Statut.CLOTUREE)
        self.assertEqual(reponse.data["effectif_final"], 8)
        self.assertEqual(
            reponse.data["message"],
            f"Le traitement du lot {self.traitement.lot.reference} a bien été clôturé.",
        )
        self.assertTrue(reponse.data["mise_en_stock_possible"])
        # Sans mise en stock, le lot reste en traitement et les saisies sont bloquées.
        self.assertEqual(
            Lot.objects.get(pk=self.traitement.lot_id).stade, Lot.Stade.EN_TRAITEMENT
        )
        self.assertTrue(hapa_est_bloque(self.hapa1.pk))
        self.assertTrue(hapa_est_bloque(self.hapa2.pk))

    def test_apercu_d_un_traitement_deja_cloture(self):
        self._client("Administrateur").post(f"{URL}{self.traitement.pk}/cloturer/")
        reponse = self._client("Employé").get(f"{URL}{self.traitement.pk}/apercu-cloture/")
        self.assertEqual(reponse.status_code, 200, reponse.data)
        self.assertFalse(reponse.data["cloturable"])
        self.assertEqual(reponse.data["raison_refus"], "Ce traitement est déjà clôturé.")

    def test_employe_lit_le_traitement(self):
        client = self._client("Employé")
        self.assertEqual(client.get(URL).status_code, 200)
        self.assertEqual(client.get(f"{URL}{self.traitement.pk}/").status_code, 200)
        self.assertEqual(client.get(f"{URL}{self.traitement.pk}/effectifs/").status_code, 200)

    def _traitement_passe(self, reference, *, ouvert_il_y_a=20, mortalite_il_y_a=15):
        """Traitement ouvert dans le passé sur un hapa neuf, avec 3 morts datés."""
        bassin = self._bassin(f"TRT-{reference}")
        hapa = self._hapa(bassin, f"{reference}-H")
        traitement = self._creer_traitement(
            bassin=bassin,
            date_ouverture=self.today - timedelta(days=ouvert_il_y_a),
            hapas=[{"id_hapa": hapa.pk, "effectif_initial": 10}],
        )
        enregistrer_mortalite_perte(
            bassin=bassin,
            hapa=hapa,
            espece=Lot.Espece.CARPE,
            type_evenement=EvenementMortalitePerte.TypeEvenement.MORTALITE,
            quantite=3,
            date_evenement=self.today - timedelta(days=mortalite_il_y_a),
            saisi_par=self.user,
            cause_mortalite=CauseMortalite.objects.get_or_create(libelle="Cause datée")[0],
        )
        return traitement

    def test_cloture_a_une_date_passee(self):
        traitement = self._traitement_passe("PASSE")
        jour = (self.today - timedelta(days=10)).isoformat()
        reponse = self._client("Directeur").post(
            f"{URL}{traitement.pk}/cloturer/", {"date_cloture": jour}, format="json"
        )
        self.assertEqual(reponse.status_code, 200, reponse.data)
        self.assertEqual(reponse.data["date_cloture"], jour)
        self.assertEqual(reponse.data["effectif_final"], 7)

    def test_date_de_cloture_refusee(self):
        traitement = self._traitement_passe("REFUS", mortalite_il_y_a=5)
        directeur = self._client("Directeur")
        for il_y_a in (-1, 21, 10):  # future, avant l'ouverture, avant la mortalité
            with self.subTest(il_y_a=il_y_a):
                jour = (self.today - timedelta(days=il_y_a)).isoformat()
                reponse = directeur.post(
                    f"{URL}{traitement.pk}/cloturer/", {"date_cloture": jour}, format="json"
                )
                self.assertEqual(reponse.status_code, 400, reponse.data)
                self.assertIn("date_cloture", reponse.data)
        self.assertEqual(
            directeur.post(f"{URL}{traitement.pk}/cloturer/", {"date_cloture": "abc"}, format="json").status_code,
            400,
        )
        traitement.refresh_from_db()
        self.assertEqual(traitement.statut, Traitement.Statut.EN_COURS)

    def test_apercu_a_la_date_demandee(self):
        traitement = self._traitement_passe("APERCU")
        jour = (self.today - timedelta(days=10)).isoformat()
        reponse = self._client("Employé").get(
            f"{URL}{traitement.pk}/apercu-cloture/", {"date_cloture": jour}
        )
        self.assertEqual(reponse.status_code, 200, reponse.data)
        self.assertEqual(reponse.data["date_cloture"], jour)
        self.assertEqual(reponse.data["duree_jours"], 10)
        self.assertEqual(reponse.data["effectif_final"], 7)
        self.assertTrue(reponse.data["cloturable"])

    def test_apercu_d_un_traitement_deja_cloture_montre_sa_vraie_cloture(self):
        traitement = self._traitement_passe("DEJA")
        jour = (self.today - timedelta(days=10)).isoformat()
        directeur = self._client("Directeur")
        self.assertEqual(
            directeur.post(f"{URL}{traitement.pk}/cloturer/", {"date_cloture": jour}, format="json").status_code, 200
        )
        reponse = directeur.get(f"{URL}{traitement.pk}/apercu-cloture/")
        self.assertEqual(reponse.status_code, 200, reponse.data)
        self.assertFalse(reponse.data["cloturable"])
        self.assertEqual(reponse.data["date_cloture"], jour)
        self.assertEqual(reponse.data["duree_jours"], 10)
        self.assertEqual(reponse.data["effectif_final"], 7)

    def test_non_connecte_refuse(self):
        self.assertEqual(
            APIClient().get(f"{URL}{self.traitement.pk}/apercu-cloture/").status_code, 401
        )


# Les tests hérités sont déjà exécutés par TraitementHapaTests : on ne garde ici
# que les tests de la clôture.
for _nom in dir(TraitementHapaTests):
    if _nom.startswith("test_"):
        setattr(ClotureTraitementTests, _nom, None)
