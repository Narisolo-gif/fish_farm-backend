"""Outils communs aux tests de droits par rôle."""
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.core.models import Role


User = get_user_model()


class ClientsParRole:
    """Mixin : crée un client authentifié par rôle (compte actif)."""

    def client_de(self, libelle, *, statut="ACTIF"):
        compteur = getattr(self, "_compteur_clients", 0) + 1
        self._compteur_clients = compteur
        utilisateur = User.objects.create_user(
            username=f"droits-{compteur}",
            password="test-password",
            role=Role.objects.get_or_create(libelle=libelle)[0],
            statut=statut,
        )
        client = APIClient()
        client.force_authenticate(utilisateur)
        client.utilisateur = utilisateur
        return client


def creer_lot_par_api(test, client, suffixe, *, parcours="SANS_TRAITEMENT", alevins=1000):
    """Reproduction → ponte → éclosion → lot par l'API (client du pilotage) ; renvoie l'id du lot."""
    from apps.bassins.models import Bassin

    bassin = Bassin.objects.create(
        reference=f"REP-{suffixe}",
        categorie=Bassin.Categorie.REPRODUCTION,
        capacity=10000,
        statut=Bassin.Statut.ACTIF,
    )
    etapes = (
        ("/api/v1/reproduction/reproductions/", {
            "reference": f"REPRO-{suffixe}", "bassin": bassin.pk, "espece": "CARPE",
            "date_ajout": "2026-01-01", "quantite_male": 5, "quantite_femelle": 5,
        }),
        ("/api/v1/reproduction/pontes/", {
            "reference": f"PONTE-{suffixe}", "bassin": bassin.pk,
            "date_ponte": "2026-01-02", "quantite_oeuf_obtenu": "5000.00",
        }),
        ("/api/v1/ecloserie/", {
            "reference": f"ECLOSION-{suffixe}", "reference_ponte": f"PONTE-{suffixe}",
            "date_eclosion": "2026-01-03", "quantite_oeufs_eclos": "5000.00",
            "quantite_alevins_obtenu": alevins,
        }),
    )
    for url, donnees in etapes:
        reponse = client.post(url, donnees, format="json")
        test.assertEqual(reponse.status_code, 201, reponse.data)
    lot = client.post("/api/v1/lots/", {
        "reference": f"LOT-{suffixe}", "id_eclosion": reponse.data["id"],
        "date_creation": "2026-01-04T12:00:00Z", "parcours_production": parcours,
    }, format="json")
    test.assertEqual(lot.status_code, 201, lot.data)
    return lot.data["id"]
