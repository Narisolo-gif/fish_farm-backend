from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bassins.models import Bassin, Hapa
from apps.core.models import Role
from apps.ecloserie.models import Eclosion
from rest_framework.exceptions import ValidationError

from apps.effectifs.models import MouvementEffectif, SortieStock
from apps.effectifs.services import (
    annuler_mouvement,
    effectif_vivant,
    enregistrer_mouvement,
)
from apps.grossissement.calculs import taille_echantillon_cible
from apps.grossissement.models import LotGrossissement, Pesee
from apps.grossissement.services import (
    cloturer_lot_grossissement,
    creer_pesee,
    valider_sortie_grossissement,
    verifier_capacite_bassin,
)
from apps.lots.models import Lot
from apps.provende.models import Provende, TypeProvende
from apps.reproduction.models import Ponte, Reproduction
from apps.stocks.services import creer_mise_en_stock
from apps.traitement.models import Traitement
from apps.traitement.services import cloturer_traitement, creer_traitement


User = get_user_model()

URL_LOTS = "/api/v1/grossissement/lots-grossissement/"
URL_APERCU = f"{URL_LOTS}apercu-sortie/"


class OutilsLotGrossissement:
    """setUp et assistants partagés : lot en stock, sortie validée, lot de grossissement."""

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

    def _creer_lot(
        self,
        reference,
        *,
        parcours=None,
        espece=Lot.Espece.CARPE,
        quantite_initiale=20,
    ):
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
            quantite_initiale=quantite_initiale,
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
            quantite_initiale=max(20, quantite),
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

    def _donnees_sortie(
        self,
        lot,
        quantite,
        *,
        il_y_a_jours=0,
        destination=None,
        entree=None,
        poids_moyen=Decimal("20"),
        **extra,
    ):
        """Données complètes : sortie, pesée initiale P0 (cible atteinte) et lot."""
        jour = (self.today - timedelta(days=il_y_a_jours)).isoformat()
        nombre = taille_echantillon_cible(quantite, 10)
        donnees = {
            "id_lot": lot.pk,
            "id_bassin_destination": (destination or self.bassin_destination).pk,
            "date_pesee_initiale": jour,
            "pourcentage_echantillon": "10",
            "nombre_poissons_peses": nombre,
            "poids_total_g": str(poids_moyen * nombre),
            "date_mise_en_place": jour,
            "effectif_entrant": quantite if entree is None else entree,
        }
        if entree is not None and entree != quantite:
            donnees["justification_ecart"] = "Écart constaté au comptage"
        donnees.update(extra)
        return donnees

    def _valider(self, donnees):
        return self.client.post(URL_LOTS, donnees, format="json")

    def _annuler(self, sortie_id, motif="Correction"):
        return self.client.post(
            f"/api/v1/effectifs/sorties-stock/{sortie_id}/annuler/",
            {"motif": motif},
            format="json",
        )

    def _etat(self):
        """Compteurs servant à vérifier qu'un refus n'a rien écrit."""
        return (
            MouvementEffectif.objects.count(),
            SortieStock.objects.count(),
            LotGrossissement.objects.count(),
        )

    def _assert_rien_ecrit(self, etat_avant, lot):
        self.assertEqual(self._etat(), etat_avant)
        self.assertEqual(Lot.objects.get(pk=lot.pk).stade, Lot.Stade.EN_STOCK)

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
        validated = self._valider(self._donnees_sortie(
            lot,
            quantite,
            il_y_a_jours=il_y_a_jours,
            destination=destination,
            entree=entree,
        ))
        self.assertEqual(validated.status_code, 201, validated.data)
        return lot, mise_en_stock, validated.data["id_sortie_stock"], validated.data


class LotGrossissementTests(OutilsLotGrossissement, TestCase):
    def test_validation_cree_lot_grossissement_et_change_le_stade(self):
        lot, _, sortie_id, payload = self._sortie_validee("LOT-VALIDATION", 10)
        grossissement = LotGrossissement.objects.get(sortie_stock_id=sortie_id)

        self.assertRegex(grossissement.code, r"^GR-\d{4}-\d{4,}$")
        self.assertEqual(grossissement.lot_origine_id, lot.pk)
        self.assertEqual(grossissement.bassin_id, self.bassin_destination.pk)
        self.assertEqual(grossissement.espece, lot.espece)
        self.assertEqual(grossissement.effectif_initial, 10)
        self.assertEqual(grossissement.statut, LotGrossissement.Statut.ACTIF)
        # Poids moyen initial calculé depuis la pesée initiale P0, tracée sur le lot.
        self.assertEqual(grossissement.poids_moyen_initial, Decimal("20.00"))
        self.assertEqual(grossissement.date_pesee_initiale, self.today)
        self.assertEqual(grossissement.pourcentage_echantillon_initial, Decimal("10"))
        self.assertEqual(grossissement.nombre_poissons_peses_initial, 1)
        self.assertEqual(grossissement.poids_total_initial_g, Decimal("20.00"))
        self.assertEqual(payload["poids_moyen_initial"], "20.00")
        self.assertFalse(Pesee.objects.exists())
        lot.refresh_from_db()
        self.assertEqual(lot.stade, Lot.Stade.EN_GROSSISSEMENT)
        sortie = SortieStock.objects.get(pk=sortie_id)
        self.assertEqual(sortie.statut, SortieStock.Statut.VALIDEE)
        self.assertEqual(sortie.effectif_sortant, 10)
        # Mouvements de la sortie seulement (l'ENTREE de la mise en stock porte aussi le lot).
        mouvements = MouvementEffectif.objects.filter(
            operation_source_content_type=ContentType.objects.get_for_model(SortieStock),
            operation_source_object_id=sortie.pk,
        )
        self.assertEqual(
            set(mouvements.values_list("type_mouvement", "bassin_id", "quantite")),
            {
                ("SORTIE", self.bassin_stock.pk, 10),
                ("ENTREE", self.bassin_destination.pk, 10),
            },
        )

    def test_ecart_effectif_initial_est_base_sur_entree(self):
        lot, _, sortie_id, _ = self._sortie_validee(
            "LOT-ECART",
            10,
            entree=7,
        )
        grossissement = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        self.assertEqual(grossissement.effectif_initial, 7)
        self.assertNotEqual(grossissement.effectif_initial, 10)

    def test_lot_non_en_stock_refuse_sans_rien_ecrire(self):
        lot, _ = self._mettre_en_stock("LOT-MAUVAIS-STade", 10)
        lot.stade = Lot.Stade.CREEE
        lot.save(update_fields=["stade"])
        etat = self._etat()

        response = self._valider(self._donnees_sortie(lot, 10))

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("id_lot", response.data)
        self.assertEqual(self._etat(), etat)

    def test_ecart_sans_justification_ou_en_plus_refuse_sans_rien_ecrire(self):
        lot, _ = self._mettre_en_stock("LOT-ECART-REFUS", 10)
        etat = self._etat()

        sans_justification = self._valider(
            self._donnees_sortie(lot, 10, entree=9, justification_ecart="")
        )
        # Écart en plus : refusé, même justifié (effectif entrant ≤ quantité sortie).
        en_plus = self._valider(self._donnees_sortie(lot, 10, entree=11))

        self.assertEqual(sans_justification.status_code, 400, sans_justification.data)
        self.assertIn("justification_ecart", sans_justification.data)
        self.assertEqual(en_plus.status_code, 400, en_plus.data)
        self.assertIn("effectif_entrant", en_plus.data)
        self._assert_rien_ecrit(etat, lot)

    def test_echantillon_non_conforme_exige_une_confirmation(self):
        lot, _ = self._mettre_en_stock("LOT-ECHANTILLON", 20)
        donnees = self._donnees_sortie(
            lot,
            20,
            nombre_poissons_peses=1,
            poids_total_g="21.00",
        )
        etat = self._etat()

        refuse = self._valider(donnees)

        self.assertEqual(refuse.status_code, 400, refuse.data)
        self.assertIn("echantillon_non_conforme", refuse.data)
        self.assertEqual(
            refuse.data["lignes_non_conformes"][0]["taille_echantillon_cible"],
            2,
        )
        self._assert_rien_ecrit(etat, lot)

        accepte = self._valider({**donnees, "confirmer_echantillon_non_conforme": True})
        self.assertEqual(accepte.status_code, 201, accepte.data)
        self.assertEqual(accepte.data["poids_moyen_initial"], "21.00")

    def test_echantillon_superieur_a_l_effectif_entrant_refuse(self):
        lot, _ = self._mettre_en_stock("LOT-ECHANTILLON-GRAND", 10)
        etat = self._etat()

        response = self._valider(self._donnees_sortie(
            lot,
            10,
            entree=5,
            nombre_poissons_peses=6,
            poids_total_g="120.00",
        ))

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("nombre_poissons_peses", response.data)
        self._assert_rien_ecrit(etat, lot)

    def test_dates_incoherentes_refusees(self):
        lot, _ = self._mettre_en_stock("LOT-DATES", 10, il_y_a_jours=5)
        hier = (self.today - timedelta(days=1)).isoformat()
        demain = (self.today + timedelta(days=1)).isoformat()
        etat = self._etat()

        for champ, donnees in (
            # Pesée initiale postérieure à la mise en bassin.
            ("date_pesee_initiale", {
                "date_pesee_initiale": self.today.isoformat(),
                "date_mise_en_place": hier,
            }),
            ("date_mise_en_place", {"date_mise_en_place": demain}),
        ):
            with self.subTest(champ=champ):
                response = self._valider(self._donnees_sortie(lot, 10, **donnees))
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(champ, response.data)
        self._assert_rien_ecrit(etat, lot)

    def test_champs_obligatoires_et_poids_initial_g_refuses(self):
        lot, _ = self._mettre_en_stock("LOT-SANS-POIDS", 10)
        complet = self._donnees_sortie(lot, 10)
        etat = self._etat()

        for champ in (
            "date_pesee_initiale",
            "nombre_poissons_peses",
            "poids_total_g",
            "date_mise_en_place",
            "effectif_entrant",
        ):
            with self.subTest(champ=champ):
                donnees = {cle: valeur for cle, valeur in complet.items() if cle != champ}
                response = self._valider(donnees)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(champ, response.data)
        # Une seule voie d'entrée : l'ancien poids saisi seul est refusé.
        ancien = self._valider({**complet, "poids_initial_g": "20.00"})
        self.assertEqual(ancien.status_code, 400, ancien.data)
        self.assertIn("poids_initial_g", ancien.data)
        self._assert_rien_ecrit(etat, lot)

    def test_code_saisi_modifiable_et_unique(self):
        lot_a, _ = self._mettre_en_stock("LOT-CODE-A", 10)
        cree = self._valider(self._donnees_sortie(lot_a, 10, code="GR-PERSO-01"))
        self.assertEqual(cree.status_code, 201, cree.data)
        self.assertEqual(cree.data["code"], "GR-PERSO-01")

        lot_b, _ = self._mettre_en_stock(
            "LOT-CODE-B",
            10,
            espece=Lot.Espece.TILAPIA,
            hapa=self._hapa(self.bassin_stock, "STOCK-CODE"),
        )
        etat = self._etat()
        doublon = self._valider(self._donnees_sortie(lot_b, 10, code="GR-PERSO-01"))

        self.assertEqual(doublon.status_code, 400, doublon.data)
        self.assertIn("code", doublon.data)
        self._assert_rien_ecrit(etat, lot_b)

    def test_apercu_retourne_les_valeurs_et_n_ecrit_rien(self):
        # quantite_initiale du lot de test : 20 poissons au plus.
        lot, _ = self._mettre_en_stock("LOT-APERCU", 20)
        etat = self._etat()

        partiel = self.client.post(
            URL_APERCU,
            {"id_lot": lot.pk, "id_bassin_destination": self.bassin_destination.pk},
            format="json",
        )
        complet = self.client.post(
            URL_APERCU,
            {
                **self._donnees_sortie(lot, 20),
                "nombre_poissons_peses": 1,
                "poids_total_g": "20.00",
            },
            format="json",
        )

        self.assertEqual(partiel.status_code, 200, partiel.data)
        self.assertEqual(partiel.data["quantite_sortie"], 20)
        self.assertEqual(partiel.data["taille_echantillon_cible"], 2)
        self.assertEqual(partiel.data["reference_lot_origine"], lot.reference)
        self.assertRegex(partiel.data["code_propose"], r"^GR-\d{4}-\d{4,}$")
        self.assertIsNone(partiel.data["poids_moyen_initial"])
        self.assertEqual(complet.status_code, 200, complet.data)
        self.assertEqual(complet.data["poids_moyen_initial"], "20.00")
        # L'aperçu signale la non-conformité sans la refuser.
        self.assertFalse(complet.data["echantillon_conforme"])
        self._assert_rien_ecrit(etat, lot)

    def test_erreur_apres_les_premieres_ecritures_annule_tout(self):
        lot, _ = self._mettre_en_stock("LOT-ATOMICITE", 10)
        donnees = self._donnees_sortie(lot, 10)
        donnees["date_pesee_initiale"] = self.today
        donnees["date_mise_en_place"] = self.today
        donnees["poids_total_g"] = Decimal(donnees["poids_total_g"])
        donnees["pourcentage_echantillon"] = Decimal("10")
        etat = self._etat()

        with patch(
            "apps.grossissement.services.LotGrossissement.objects.create",
            side_effect=RuntimeError("panne simulée"),
        ):
            with self.assertRaises(RuntimeError):
                valider_sortie_grossissement(donnees, utilisateur=self.user)

        self._assert_rien_ecrit(etat, lot)

    def test_permissions_et_ancien_flux(self):
        lot, _ = self._mettre_en_stock("LOT-PERMISSIONS", 10)
        employe = User.objects.create_user(
            username="grossissement-employe",
            password="test-password",
            role=Role.objects.create(libelle="Employé"),
            statut="ACTIF",
        )
        client_employe = APIClient()
        client_employe.force_authenticate(employe)
        etat = self._etat()

        self.assertEqual(
            client_employe.post(URL_LOTS, self._donnees_sortie(lot, 10), format="json").status_code,
            403,
        )
        self.assertEqual(
            client_employe.post(URL_APERCU, self._donnees_sortie(lot, 10), format="json").status_code,
            403,
        )
        self.assertEqual(client_employe.get(URL_LOTS).status_code, 200)
        self.assertEqual(APIClient().get(URL_LOTS).status_code, 401)
        # Plus de brouillon ni de validation en deux temps.
        ancien = self.client.post(
            "/api/v1/effectifs/sorties-stock/",
            {"id_lot": lot.pk, "id_bassin_destination": self.bassin_destination.pk},
            format="json",
        )
        self.assertEqual(ancien.status_code, 405, ancien.data)
        self._assert_rien_ecrit(etat, lot)

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

    def test_validation_calcule_le_poids_moyen_initial(self):
        lot, _ = self._mettre_en_stock("LOT-POIDS-SAISI", 15)
        # Cible : arrondi supérieur de 10 % de 15 = 2 poissons ; 41 g / 2 = 20,50 g.
        response = self._valider(self._donnees_sortie(
            lot,
            15,
            nombre_poissons_peses=2,
            poids_total_g="41.00",
        ))
        self.assertEqual(response.status_code, 201, response.data)
        gross = LotGrossissement.objects.get(pk=response.data["id"])
        self.assertEqual(gross.poids_moyen_initial, Decimal("20.50"))
        self.assertEqual(gross.nombre_poissons_peses_initial, 2)

    def test_annulation_refuse_historique_ou_lot_cloture_sans_changement(self):
        lot_a, _, sortie_a, _ = self._sortie_validee("LOT-POIDS", 10)
        gross_a = LotGrossissement.objects.get(sortie_stock_id=sortie_a)
        creer_pesee(
            lot_id=gross_a.pk,
            date_pesee=timezone.localdate(),
            nombre=1,
            poids_total=Decimal("25.00"),
            utilisateur=self.user,
        )
        count_mouvements = MouvementEffectif.objects.count()
        refused_history = self._annuler(sortie_a)
        self.assertEqual(refused_history.status_code, 400, refused_history.data)
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

        validated = self._valider(self._donnees_sortie(lot, 10))
        self.assertEqual(validated.status_code, 201, validated.data)
        second = LotGrossissement.objects.get(pk=validated.data["id"])

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

    def _lot_a_sortir(self, reference, *, espece=Lot.Espece.CARPE, hapa=None):
        lot, _ = self._mettre_en_stock(reference, 10, espece=espece, hapa=hapa)
        return lot

    def test_creation_refuse_meme_espece_dans_le_bassin(self):
        self._sortie_validee("LOT-CARPE-1", 10)
        lot = self._lot_a_sortir(
            "LOT-CARPE-2",
            hapa=self._hapa(self.bassin_stock, "STOCK-B"),
        )
        etat = self._etat()

        refused = self._valider(self._donnees_sortie(lot, 10))

        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertIn("id_bassin_destination", refused.data)
        self._assert_rien_ecrit(etat, lot)

    def test_creation_accepte_deux_especes_differentes(self):
        self._sortie_validee("LOT-MIXTE-CARPE", 10)
        lot = self._lot_a_sortir(
            "LOT-MIXTE-TILAPIA",
            espece=Lot.Espece.TILAPIA,
            hapa=self._hapa(self.bassin_stock, "STOCK-T"),
        )

        validated = self._valider(self._donnees_sortie(lot, 10))

        self.assertEqual(validated.status_code, 201, validated.data)
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
        lot = self._lot_a_sortir(
            "LOT-APRES-CLOTURE",
            hapa=self._hapa(self.bassin_stock, "STOCK-C"),
        )

        validated = self._valider(self._donnees_sortie(lot, 10))

        self.assertEqual(validated.status_code, 201, validated.data)

    def test_creation_refuse_mise_en_place_anterieure_a_la_cloture(self):
        _, _, premiere, _ = self._sortie_validee("LOT-CLOS-AUJOURDHUI", 10)
        cloturer_lot_grossissement(
            LotGrossissement.objects.get(sortie_stock_id=premiere).pk,
            motif="Fin du cycle",
            poids_final_g=Decimal("250"),
            utilisateur=self.user,
        )
        lot, _ = self._mettre_en_stock(
            "LOT-ANTIDATE",
            10,
            il_y_a_jours=4,
            hapa=self._hapa(self.bassin_stock, "STOCK-D"),
        )
        etat = self._etat()

        refused = self._valider(self._donnees_sortie(lot, 10, il_y_a_jours=3))

        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertIn("date_mise_en_place", refused.data)
        self._assert_rien_ecrit(etat, lot)

    def test_annulation_mouvement_refusee_apres_cloture(self):
        _, _, sortie_id, _ = self._sortie_validee("LOT-FIGE", 10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        mortalite = self._mortalite(2, self.today)
        cloturer_lot_grossissement(
            gross.pk,
            motif="Fin du cycle",
            poids_final_g=Decimal("250"),
            utilisateur=self.user,
        )
        evacuation = MouvementEffectif.objects.get(
            bassin=self.bassin_destination,
            type_mouvement=MouvementEffectif.TypeMouvement.SORTIE,
        )
        count_mouvements = MouvementEffectif.objects.count()

        for mouvement in (mortalite, evacuation):
            with self.subTest(type=mouvement.type_mouvement):
                with self.assertRaises(ValidationError) as ctx:
                    annuler_mouvement(
                        mouvement.pk,
                        motif_annulation="Erreur de saisie",
                        saisi_par=self.user,
                    )
                self.assertIn("clôturé", str(ctx.exception.detail))
        self.assertEqual(MouvementEffectif.objects.count(), count_mouvements)
        self.assertEqual(
            effectif_vivant(self.bassin_destination.pk, Lot.Espece.CARPE),
            0,
        )

    def test_creation_refuse_bassin_avec_hapa_actif(self):
        lot = self._lot_a_sortir("LOT-HAPA-GROSS")
        Hapa.objects.create(
            bassin=self.bassin_destination,
            nom="GROSS-HAPA",
            statut=Hapa.Statut.ACTIF,
        )
        etat = self._etat()

        refused = self._valider(self._donnees_sortie(lot, 10))

        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertIn("hapa", str(refused.data["id_bassin_destination"]))
        self._assert_rien_ecrit(etat, lot)

    def test_capacite_refuse_un_troisieme_lot(self):
        for reference in ("LOT-CAP-1", "LOT-CAP-2"):
            self._sortie_validee(reference, 10)
            LotGrossissement.objects.filter(
                lot_origine__reference=reference
            ).update(espece=Lot.Espece.TILAPIA if reference.endswith("1") else "AUTRE")
        with self.assertRaises(ValidationError) as ctx:
            verifier_capacite_bassin(self.bassin_destination, Lot.Espece.CARPE)
        self.assertIn("deux lots", str(ctx.exception.detail["id_bassin_destination"]))

    def test_patch_refuse_poids_initial_et_trace_de_la_pesee_initiale(self):
        _, _, sortie_id, _ = self._sortie_validee("LOT-POIDS-INITIAL", 10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        url = f"{URL_LOTS}{gross.pk}/"

        for data in (
            {"poids_moyen_initial": "12.50"},
            # Ancien nom du champ : refusé lui aussi.
            {"poids_initial_g": "12.50"},
            {"date_pesee_initiale": self.today.isoformat()},
            {"pourcentage_echantillon_initial": "20"},
            {"nombre_poissons_peses_initial": 3},
            {"poids_total_initial_g": "60.00"},
            {"code": "GR-AUTRE"},
        ):
            with self.subTest(data=data):
                refused = self.client.patch(url, data, format="json")
                self.assertEqual(refused.status_code, 400, refused.data)
                self.assertIn(next(iter(data)), refused.data)
        gross.refresh_from_db()
        self.assertEqual(gross.poids_moyen_initial, Decimal("20.00"))
        self.assertEqual(gross.nombre_poissons_peses_initial, 1)

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
        # J30, J60 puis tous les 15 jours, sans limite de durée. Sans pesée, la
        # prochaine pesée reste J30, en retard une fois l'échéance passée.
        _, _, sortie_id, _ = self._sortie_validee("LOT-CYCLE", 10)
        gross = LotGrossissement.objects.get(sortie_stock_id=sortie_id)
        for age, etape, en_retard in (
            (0, 1, False),
            (29, 1, False),
            (30, 2, False),
            (31, 2, True),
            (75, 4, True),
            (180, 11, True),
            (400, 25, True),
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
                    self.assertEqual(gross.jour_de_cycle(self.today), age)
                    self.assertEqual(
                        gross.prochaine_echeance_pesee,
                        self.today + timedelta(days=30 - age),
                    )
                    self.assertEqual(gross.pesee_en_retard, en_retard)

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
