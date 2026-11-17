"""Structure de la collection « flux complet » (sans base de données).

Lancement : py -m unittest discover -s tests/postman -p "test_generer*.py"
"""
import importlib.util
import pathlib
import unittest


CHEMIN = pathlib.Path(__file__).with_name("generer_flux_complet.py")
_spec = importlib.util.spec_from_file_location("generer_flux_complet", CHEMIN)
gfc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gfc)


def etapes():
    for _, liste in gfc.construire_dossiers():
        yield from liste


def requetes(collection):
    for dossier in collection["item"]:
        yield from dossier["item"]


class StructureCollectionTests(unittest.TestCase):
    def setUp(self):
        self.collection = gfc.generer_collection(gfc.construire_dossiers())

    def test_chaque_requete_porte_le_jeton_de_son_role(self):
        for etape, requete in zip(etapes(), requetes(self.collection)):
            with self.subTest(etape=etape.nom):
                auth = requete["request"]["auth"]
                if etape.role is None:
                    self.assertEqual(auth["type"], "noauth")
                else:
                    self.assertEqual(auth["bearer"][0]["value"], "{{fc_token_" + etape.role + "}}")

    def test_aucune_etape_nominale_en_administrateur(self):
        self.assertEqual([e.nom for e in etapes() if e.role == "admin"], [])

    def test_refus_attendent_403_ou_401(self):
        for etape in etapes():
            if etape.refus:
                with self.subTest(etape=etape.nom):
                    self.assertIn(etape.attendu, (401, 403))
                    self.assertEqual(etape.capture, {})

    def test_variables_prefixees_fc(self):
        for etape in etapes():
            for variable in etape.capture:
                self.assertTrue(variable.startswith("fc_"), (etape.nom, variable))

    def test_collection_v21_valide(self):
        self.assertIn("v2.1.0", self.collection["info"]["schema"])
        for requete in requetes(self.collection):
            with self.subTest(requete=requete["name"]):
                self.assertTrue(any(e["listen"] == "test" for e in requete["event"]))
                self.assertTrue(requete["request"]["url"].startswith("{{base_url}}/api/"))

    def test_lire_un_champ_ou_un_element_de_liste(self):
        donnees = [{"type": "TRAITEMENT", "id": 3}, {"type": "CROISSANCE", "id": 7}]
        self.assertEqual(gfc.lire(donnees, "[type=CROISSANCE].id"), 7)
        self.assertEqual(gfc.lire(donnees, "0.id"), 3)
        self.assertEqual(gfc.lire({"access": "x"}, "access"), "x")
        self.assertIsNone(gfc.lire(donnees, "[type=INCONNU].id"))

    def test_noms_de_requete_uniques(self):
        noms = [requete["name"] for requete in requetes(self.collection)]
        self.assertEqual(len(noms), len(set(noms)))


if __name__ == "__main__":
    unittest.main()
