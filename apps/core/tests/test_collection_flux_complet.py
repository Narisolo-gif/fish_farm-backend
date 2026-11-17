"""Rejoue la collection Postman « flux complet » avec les vrais droits (JWT).

Le scénario est celui de tests/postman/generer_flux_complet.py : chaque étape
est envoyée avec le jeton du rôle indiqué, son code HTTP est vérifié, ses
identifiants sont capturés pour les étapes suivantes, et un refus ne doit
rien écrire en base.
"""
import importlib.util
import json
import re
import uuid
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Role


DOSSIER_POSTMAN = Path(settings.BASE_DIR) / "tests" / "postman"
_spec = importlib.util.spec_from_file_location(
    "generer_flux_complet", DOSSIER_POSTMAN / "generer_flux_complet.py"
)
gfc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gfc)

MODELES_SURVEILLES = (
    "lots.Lot", "effectifs.MouvementEffectif", "effectifs.EvenementMortalitePerte",
    "provende.ConsommationProvende", "grossissement.Pesee", "environnement.ParametreEnvironnemental",
    "traitement.Traitement", "traitement.TraitementHapa", "stocks.MiseEnStock", "grossissement.LotGrossissement",
    "grossissement.IndicateurLot", "core.Utilisateur", "bassins.Bassin", "bassins.Hapa", "effectifs.BlocageSaisie",
    "effectifs.SortieStock", "provende.Provende",
)
COMPTES = (
    ("admin_username", "admin_password", "Administrateur"),
    ("director_username", "director_password", "Directeur"),
    ("manager_username", "manager_password", "Gestionnaire"),
    ("employee_username", "employee_password", "Employé"),
)
MOTIF_VARIABLE = re.compile(r"\{\{(\w+)\}\}")


def variables_environnement():
    donnees = json.loads(
        (DOSSIER_POSTMAN / "pisciculture_local.postman_environment.json").read_text(encoding="utf-8")
    )
    return {v["key"]: v["value"] for v in donnees["values"]}


def empreinte():
    """Contenu complet des tables surveillées : un refus ne doit ni insérer, ni modifier, ni supprimer."""
    return {nom: list(apps.get_model(nom).objects.order_by("pk").values_list()) for nom in MODELES_SURVEILLES}


def remplacer(texte, variables, etape):
    def valeur(correspondance):
        nom = correspondance.group(1)
        if nom not in variables:
            raise AssertionError(f"{etape} : variable {{{{{nom}}}}} non définie")
        return str(variables[nom])

    return MOTIF_VARIABLE.sub(valeur, texte)


def rejouer(test, dossiers, variables):
    """Exécute le scénario ; renvoie les variables capturées."""
    client = APIClient()
    for dossier, etapes in dossiers:
        for etape in etapes:
            nom = f"{dossier} / {etape.nom}"
            chemin = remplacer(etape.chemin, variables, nom)
            corps = (
                json.loads(remplacer(json.dumps(etape.corps, ensure_ascii=False), variables, nom))
                if etape.corps is not None
                else None
            )
            entetes = {}
            if etape.role is not None:
                entetes["HTTP_AUTHORIZATION"] = "Bearer " + variables["fc_token_" + etape.role]
            avant = empreinte() if etape.refus else None
            reponse = client.generic(
                etape.methode,
                chemin,
                data=json.dumps(corps) if corps is not None else "",
                content_type="application/json",
                **entetes,
            )
            test.assertEqual(
                reponse.status_code, etape.attendu,
                f"{nom} : {etape.methode} {chemin} → {reponse.status_code} {reponse.content[:600]!r}",
            )
            if etape.refus:
                test.assertTrue(empreinte() == avant, f"{nom} : un refus a écrit en base")
            if etape.verifs:
                donnees = reponse.json()
                for libelle, chemin_champ, attendu in etape.verifs:
                    attendu = remplacer(gfc.texte(attendu), variables, nom)
                    test.assertEqual(
                        gfc.texte(gfc.lire(donnees, chemin_champ)), attendu,
                        f"{nom} : vérification « {libelle} » ({chemin_champ})",
                    )
            if etape.capture:
                donnees = reponse.json()
                for variable, champ in etape.capture.items():
                    valeur = gfc.lire(donnees, champ)
                    test.assertIsNotNone(valeur, f"{nom} : capture {variable} ({champ}) vide")
                    variables[variable] = valeur
    return variables


class CollectionFluxCompletTests(TestCase):
    def setUp(self):
        self.environnement = variables_environnement()
        for cle_nom, cle_mdp, libelle in COMPTES:
            get_user_model().objects.create_user(
                username=self.environnement[cle_nom],
                password=self.environnement[cle_mdp],
                role=Role.objects.get_or_create(libelle=libelle)[0],
                statut="ACTIF",
            )

    def _rejouer(self):
        variables = {**self.environnement, "fc_run": f"FC-{uuid.uuid4().hex[:8].upper()}"}
        return rejouer(self, gfc.construire_dossiers(), variables)

    def test_scenario_rejoue_deux_fois(self):
        from apps.grossissement.models import LotGrossissement
        from apps.lots.models import Lot
        from apps.traitement.models import Traitement

        variables = self._rejouer()
        lot_p = Lot.objects.get(pk=variables["fc_lot_p"])
        self.assertEqual(lot_p.stade, Lot.Stade.CLOTUREE)
        self.assertIsNotNone(LotGrossissement.objects.get(pk=variables["fc_lgr_p"]).evacue_le)
        self.assertEqual(Traitement.objects.get(pk=variables["fc_trt_t1"]).statut, Traitement.Statut.EN_COURS)
        self.assertEqual(Lot.objects.get(pk=variables["fc_lot_t2"]).stade, Lot.Stade.EN_STOCK)
        for cle in ("fc_lgr_g1", "fc_lgr_g2"):
            lot = LotGrossissement.objects.get(pk=variables[cle])
            self.assertEqual(lot.statut, LotGrossissement.Statut.ACTIF)
            self.assertTrue(lot.pesee_en_retard, cle)
        # Deuxième exécution sur la même base : références uniques, tout repasse.
        self._rejouer()

    def test_empreinte_voit_une_mise_a_jour(self):
        """Un refus qui modifierait une ligne existante (PATCH) doit être détecté, pas seulement une insertion."""
        compte = get_user_model().objects.get(username=self.environnement["manager_username"])
        avant = empreinte()
        compte.role = Role.objects.get_or_create(libelle="Administrateur")[0]
        compte.save()
        self.assertNotEqual(empreinte(), avant)

    def test_scenario_rejoue_avec_les_vrais_droits(self):
        variables = self._rejouer()
        for role in ("admin", "directeur", "gestionnaire", "employe"):
            self.assertIn(f"fc_token_{role}", variables)
