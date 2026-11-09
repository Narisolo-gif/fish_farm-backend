from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bassins.models import Bassin, Hapa
from apps.core.models import Role
from apps.effectifs.models import MouvementEffectif
from apps.effectifs.services import bloquer_hapa, enregistrer_mouvement

from apps.provende.models import ConsommationProvende, Provende, TypeProvende
from apps.provende.services import (
    MAX_DISTRIBUTIONS_PAR_JOUR,
    annuler_distribution,
    enregistrer_distribution,
    jours_manquants,
    total_journalier,
)

User = get_user_model()


class ConsommationProvendeTests(TestCase):
    def setUp(self):
        role, _ = Role.objects.get_or_create(
            libelle="Administrateur",
            defaults={"description": "Tests de consommation"},
        )
        self.user = User.objects.create_user(
            username="provende-admin",
            password="test-password",
            role=role,
            statut="ACTIF",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.today = timezone.localdate()
        self.treatment = self._bassin("CONS-TRAIT", Bassin.Categorie.TRAITEMENT)
        self.treatment_hapa = self._hapa(self.treatment, "TRAIT-A")
        self.treatment_hapa2 = self._hapa(self.treatment, "TRAIT-B")
        self.stock = self._bassin("CONS-STOCK", Bassin.Categorie.STOCKS)
        self.stock_hapa = self._hapa(self.stock, "STOCK-A")
        self.stock_hapa2 = self._hapa(self.stock, "STOCK-B")
        self.reproduction = self._bassin(
            "CONS-REPRO",
            Bassin.Categorie.REPRODUCTION,
        )
        self.grossissement = self._bassin(
            "CONS-GROSS",
            Bassin.Categorie.GROSSISSEMENT,
        )
        self.type_croissance, _ = TypeProvende.objects.get_or_create(
            type=TypeProvende.Type.CROISSANCE,
        )
        self.type_demarrage, _ = TypeProvende.objects.get_or_create(
            type=TypeProvende.Type.DEMARRAGE,
        )
        self.feed_number = 0
        self._ajouter_effectif(self.treatment, self.treatment_hapa)
        self._ajouter_effectif(self.treatment, self.treatment_hapa2)
        self._ajouter_effectif(self.stock, self.stock_hapa)
        self._ajouter_effectif(self.stock, self.stock_hapa2)

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

    def _ajouter_effectif(self, bassin, hapa=None, quantity=20, day=None):
        return enregistrer_mouvement(
            bassin=bassin,
            hapa=hapa,
            espece="CARPE",
            type_mouvement=MouvementEffectif.TypeMouvement.ENTREE,
            quantite=quantity,
            date_evenement=day or self.today,
            saisi_par=self.user,
        )

    def _provende(self, type_provende=None):
        self.feed_number += 1
        return Provende.objects.create(
            type_provende=type_provende or self.type_croissance,
            reference=f"CONS-FEED-{self.feed_number}",
            date_creation=self.today - timedelta(days=1),
        )

    def _date(self, offset_minutes=0):
        debut_jour = timezone.make_aware(
            datetime.combine(self.today, time.min),
            timezone.get_current_timezone(),
        )
        valeur = timezone.localtime().replace(second=0, microsecond=0)
        valeur -= timedelta(minutes=offset_minutes + 1)
        return max(valeur, debut_jour)

    def _post(
        self,
        *,
        bassin=None,
        hapa=None,
        provende=None,
        date_distribution=None,
        quantite_kg="1.25",
    ):
        data = {
            "id_bassin": (bassin or self.treatment).pk,
            "id_provende": (provende or self._provende()).pk,
            "quantite_kg": quantite_kg,
        }
        if hapa is not None:
            data["id_hapa"] = hapa.pk
        if date_distribution is not None:
            data["date_distribution"] = date_distribution.isoformat()
        return self.client.post(
            "/api/v1/provende/consommations-provende/",
            data,
            format="json",
        )

    def _create(self, *, hapa=None, provende=None, date_distribution=None, quantity="1.25"):
        return enregistrer_distribution(
            {
                "bassin": self.treatment,
                "hapa": hapa or self.treatment_hapa,
                "provende": provende or self._provende(),
                "date_distribution": date_distribution or self._date(),
                "quantite_kg": Decimal(quantity),
            },
            self.user,
        )

    def test_api_creation_sur_hapa_de_traitement(self):
        response = self._post(
            hapa=self.treatment_hapa,
            date_distribution=self._date(),
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["id_hapa"], self.treatment_hapa.pk)
        self.assertEqual(response.data["nom_hapa"], self.treatment_hapa.nom)
        self.assertEqual(response.data["statut"], ConsommationProvende.Statut.VALIDE)

    def test_trois_distributions_autorisees_et_zero_kg_compte(self):
        for index in range(MAX_DISTRIBUTIONS_PAR_JOUR - 1):
            response = self._post(
                hapa=self.treatment_hapa,
                date_distribution=self._date(index),
            )
            self.assertEqual(response.status_code, 201, response.data)

        troisieme = self._post(
            hapa=self.treatment_hapa,
            date_distribution=self._date(2),
            quantite_kg="0.00",
        )
        quatrieme = self._post(
            hapa=self.treatment_hapa,
            date_distribution=self._date(3),
        )

        self.assertEqual(troisieme.status_code, 201, troisieme.data)
        self.assertEqual(quatrieme.status_code, 400, quatrieme.data)
        self.assertIn("date_distribution", quatrieme.data)
        self.assertIn("Maximum de 3", str(quatrieme.data))

    def test_plafond_par_hapa_et_toutes_provendes_confondues(self):
        dates = [self._date(index) for index in range(3)]
        for index, provende_type in enumerate(
            (self.type_croissance, self.type_demarrage, self.type_croissance)
        ):
            response = self._post(
                hapa=self.treatment_hapa,
                provende=self._provende(provende_type),
                date_distribution=dates[index],
            )
            self.assertEqual(response.status_code, 201, response.data)

        refusee = self._post(
            hapa=self.treatment_hapa,
            provende=self._provende(self.type_demarrage),
            date_distribution=self._date(4),
        )
        autre_hapa = [
            self._post(
                hapa=self.treatment_hapa2,
                date_distribution=self._date(10 + index),
            )
            for index in range(3)
        ]

        self.assertEqual(refusee.status_code, 400, refusee.data)
        self.assertTrue(all(response.status_code == 201 for response in autre_hapa))

    def test_annulation_libere_une_place(self):
        lignes = [
            self._create(date_distribution=self._date(index))
            for index in range(3)
        ]
        annuler_distribution(
            lignes[0].pk,
            motif="Erreur de saisie",
            utilisateur=self.user,
        )

        response = self._post(
            hapa=self.treatment_hapa,
            date_distribution=self._date(4),
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            ConsommationProvende.objects.filter(
                hapa=self.treatment_hapa,
                statut=ConsommationProvende.Statut.VALIDE,
            ).count(),
            3,
        )

    def test_doublon_meme_minute_refuse(self):
        date_distribution = self._date().replace(second=10, microsecond=200)
        first = self._post(
            hapa=self.treatment_hapa,
            date_distribution=date_distribution,
        )
        duplicate = self._post(
            hapa=self.treatment_hapa,
            date_distribution=date_distribution.replace(second=50, microsecond=900),
            provende=Provende.objects.get(pk=first.data["id_provende"]),
        )

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertIn("date_distribution", duplicate.data)

    def test_hapa_obligatoire_en_traitement_et_stocks(self):
        treatment_without_hapa = self._bassin(
            "CONS-TRAIT-SANS",
            Bassin.Categorie.TRAITEMENT,
        )
        response = self._post(
            bassin=treatment_without_hapa,
            date_distribution=self._date(),
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("créez d'abord des hapas", str(response.data))

        for bassin in (self.treatment, self.stock):
            response = self._post(bassin=bassin, date_distribution=self._date(1))
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn("id_hapa", response.data)
            self.assertIn("obligatoire", str(response.data["id_hapa"]))

    def test_hapa_facultatif_en_grossissement_sans_hapa(self):
        self._ajouter_effectif(self.grossissement, quantity=5)

        response = self._post(
            bassin=self.grossissement,
            date_distribution=self._date(),
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["id_hapa"])

    def test_hapa_facultatif_accepte_en_reproduction(self):
        hapa = self._hapa(self.reproduction, "REPRO-A")
        self._ajouter_effectif(self.reproduction, hapa, quantity=5)

        response = self._post(
            bassin=self.reproduction,
            hapa=hapa,
            date_distribution=self._date(),
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["id_hapa"], hapa.pk)

    def test_hapa_facultatif_invalide_refuse(self):
        self._ajouter_effectif(self.grossissement, quantity=5)
        inactif = self._hapa(self.grossissement, "GROSS-INACTIF", actif=False)

        for hapa in (self.treatment_hapa, inactif):
            response = self._post(
                bassin=self.grossissement,
                hapa=hapa,
                date_distribution=self._date(),
            )
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn("id_hapa", response.data)

    def test_jours_manquants_grossissement_sans_hapa(self):
        self._ajouter_effectif(
            self.grossissement,
            quantity=5,
            day=self.today - timedelta(days=1),
        )
        self._post(bassin=self.grossissement, date_distribution=self._date())

        resultats = jours_manquants(
            self.grossissement.pk,
            self.today - timedelta(days=1),
            self.today,
        )

        self.assertEqual(resultats, [self.today - timedelta(days=1)])

    def test_hapa_d_un_autre_bassin_ou_inactif_refuse(self):
        autre_bassin = self._bassin(
            "CONS-TRAIT-AUTRE",
            Bassin.Categorie.TRAITEMENT,
        )
        autre_hapa = self._hapa(autre_bassin, "AUTRE")
        inactif = self._hapa(self.treatment, "INACTIF", actif=False)

        for hapa in (autre_hapa, inactif):
            response = self._post(
                hapa=hapa,
                date_distribution=self._date(),
            )
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn("id_hapa", response.data)

    def test_distribution_possible_sur_hapa_bloque_apres_cloture(self):
        blocage = bloquer_hapa(
            self.treatment_hapa,
            self._provende(),
            "Traitement clôturé : transfert en attente",
        )
        response = self._post(
            hapa=self.treatment_hapa,
            date_distribution=self._date(),
        )

        self.assertTrue(blocage.actif)
        self.assertEqual(response.status_code, 201, response.data)

    def test_dates_et_effectif_cible_valides_meme_a_zero_kg(self):
        future = self._post(
            hapa=self.treatment_hapa,
            date_distribution=timezone.now() + timedelta(days=1),
        )
        provende_anterieure = self._provende()
        provende_anterieure.date_creation = self.today + timedelta(days=1)
        provende_anterieure.save(update_fields=["date_creation"])
        anterieure = self._post(
            hapa=self.treatment_hapa,
            provende=provende_anterieure,
            date_distribution=self._date(5),
        )
        bassin_vide = self._bassin(
            "CONS-VIDE",
            Bassin.Categorie.TRAITEMENT,
        )
        hapa_vide = self._hapa(bassin_vide, "VIDE")
        sans_effectif = self._post(
            bassin=bassin_vide,
            hapa=hapa_vide,
            date_distribution=self._date(6),
            quantite_kg="0.00",
        )

        self.assertEqual(future.status_code, 400, future.data)
        self.assertIn("date_distribution", future.data)
        self.assertEqual(anterieure.status_code, 400, anterieure.data)
        self.assertIn("id_provende", anterieure.data)
        self.assertEqual(sans_effectif.status_code, 400, sans_effectif.data)
        self.assertIn("id_hapa", sans_effectif.data)

    def test_totaux_journaliers_par_hapa_avec_detail_et_places_restantes(self):
        for provende_type, quantity, offset in (
            (self.type_croissance, "1.25", 0),
            (self.type_demarrage, "2.75", 1),
        ):
            self._create(
                provende=self._provende(provende_type),
                date_distribution=self._date(offset),
                quantity=quantity,
            )

        resultats = total_journalier(
            self.treatment.pk,
            self.today,
            self.today,
            hapa_id=self.treatment_hapa.pk,
        )

        self.assertEqual(len(resultats), 1)
        self.assertEqual(resultats[0]["total_kg"], Decimal("4.00"))
        self.assertEqual(resultats[0]["nombre_distributions"], 2)
        self.assertEqual(resultats[0]["distributions_restantes"], 1)
        self.assertEqual(
            {ligne["type_provende"] for ligne in resultats[0]["detail_par_type"]},
            {"Croissance", "Démarrage"},
        )

        response = self.client.get(
            "/api/v1/provende/consommations-provende/totaux-journaliers/",
            {
                "bassin": self.treatment.pk,
                "hapa": self.treatment_hapa.pk,
                "date_min": self.today.isoformat(),
                "date_max": self.today.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data[0]["distributions_restantes"], 1)

    def test_jours_manquants_retournes_par_hapa_actif(self):
        self._create(date_distribution=self._date())

        resultats = jours_manquants(
            self.treatment.pk,
            self.today,
            self.today,
        )

        self.assertEqual(
            resultats,
            [
                {
                    "id_hapa": self.treatment_hapa.pk,
                    "nom_hapa": self.treatment_hapa.nom,
                    "jours_manquants": [],
                },
                {
                    "id_hapa": self.treatment_hapa2.pk,
                    "nom_hapa": self.treatment_hapa2.nom,
                    "jours_manquants": [self.today],
                },
            ],
        )
        response = self.client.get(
            "/api/v1/provende/consommations-provende/jours-manquants/",
            {
                "bassin": self.treatment.pk,
                "date_min": self.today.isoformat(),
                "date_max": self.today.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data[1]["jours_manquants"],
            [self.today.isoformat()],
        )

    def test_filtre_hapa_et_annulation_api(self):
        first = self._post(
            hapa=self.treatment_hapa,
            date_distribution=self._date(),
        )
        second = self._post(
            hapa=self.treatment_hapa2,
            date_distribution=self._date(1),
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)

        filtered = self.client.get(
            "/api/v1/provende/consommations-provende/",
            {"bassin": self.treatment.pk, "hapa": self.treatment_hapa.pk},
        )
        cancelled = self.client.post(
            (
                "/api/v1/provende/consommations-provende/"
                f"{first.data['id']}/annuler/"
            ),
            {"motif": "Saisie incorrecte"},
            format="json",
        )

        self.assertEqual(filtered.status_code, 200, filtered.data)
        self.assertEqual([row["id"] for row in filtered.data], [first.data["id"]])
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(
            cancelled.data["statut"],
            ConsommationProvende.Statut.ANNULEE,
        )
