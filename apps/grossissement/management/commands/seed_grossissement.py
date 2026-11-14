"""Génère des lots de grossissement simulés dans une base de développement.

Les données sont écrites par les vues de l'API, appelées en interne : les
serializers, les services, les signaux et le recalcul des indicateurs
s'appliquent comme pour une saisie réelle. Rien n'est inséré directement
dans les tables.

Exemples :
    python manage.py seed_grossissement --dry-run
    python manage.py seed_grossissement --scenarios NORMAL,DEUX_LOTS --seed 42
"""
import os
from datetime import date, datetime, time, timedelta, timezone as dt_timezone

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.grossissement.simulation import (
    CAUSE_PAR_DEFAUT,
    CODES_SCENARIOS,
    construire_scenarios,
)


PREFIXE = "SIM-"
OBSERVATION = "Donnée simulée"


class EchecSimulation(CommandError):
    pass


class ClientInterne:
    """Appelle les vues de l'API en interne, authentifié comme l'utilisateur."""

    def __init__(self, utilisateur):
        self.utilisateur = utilisateur
        hote = next(
            (h for h in settings.ALLOWED_HOSTS if h and "*" not in h and not h.startswith(".")),
            "localhost",
        )
        self.factory = APIRequestFactory(SERVER_NAME=hote)

    def post(self, chemin, data, statuts=(200, 201)):
        requete = self.factory.post(chemin, data, format="json")
        force_authenticate(requete, user=self.utilisateur)
        correspondance = resolve(chemin)
        reponse = correspondance.func(
            requete, *correspondance.args, **correspondance.kwargs
        )
        if reponse.status_code not in statuts:
            raise EchecSimulation(
                f"POST {chemin} refusé ({reponse.status_code}) : "
                f"{getattr(reponse, 'data', '')} ; données envoyées : {data}"
            )
        return reponse.data


def _iso(valeur):
    return valeur.isoformat().replace("+00:00", "Z") if isinstance(valeur, datetime) else valeur.isoformat()


def _a_heure(jour, heure=time(6, 0)):
    return datetime.combine(jour, heure, tzinfo=dt_timezone.utc)


class Command(BaseCommand):
    help = (
        "Crée des lots de grossissement simulés (bassins SIM-<CODE>) avec leurs "
        "mortalités, provende, paramètres environnementaux et pesées. "
        "Réservé aux bases de développement."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--scenarios",
            default=",".join(CODES_SCENARIOS),
            help=f"Codes séparés par des virgules (défaut : tous). Disponibles : {', '.join(CODES_SCENARIOS)}.",
        )
        parser.add_argument(
            "--date-reference",
            help="Date de fin de la simulation, AAAA-MM-JJ (défaut : aujourd'hui).",
        )
        parser.add_argument("--seed", type=int, default=42, help="Graine aléatoire (défaut : 42).")
        parser.add_argument(
            "--utilisateur",
            help="E-mail de l'auteur des saisies (défaut : premier Administrateur actif).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche le plan et les statistiques sans toucher à la base.",
        )

    # --- Entrée ------------------------------------------------------------

    def handle(self, *args, **options):
        if not settings.DEBUG and os.environ.get("FISHFARM_ALLOW_SEED") != "1":
            raise CommandError(
                "Commande réservée au développement : DEBUG est désactivé. "
                "Définissez FISHFARM_ALLOW_SEED=1 pour l'exécuter malgré tout."
            )
        codes = [c.strip().upper() for c in options["scenarios"].split(",") if c.strip()]
        date_reference = self._date_reference(options["date_reference"])
        try:
            scenarios = construire_scenarios(codes, date_reference, options["seed"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            f"Simulation : {len(scenarios)} scénario(s), date de référence "
            f"{date_reference}, graine {options['seed']}."
        )
        if options["dry_run"]:
            self._afficher_plan(scenarios)
            self.stdout.write(self.style.WARNING("Mode --dry-run : aucune donnée écrite."))
            return

        utilisateur = self._utilisateur(options["utilisateur"])
        client = ClientInterne(utilisateur)
        resultats = []
        for scenario in scenarios:
            resultat = self._executer(client, scenario)
            if resultat:
                resultats.append(resultat)
        self._afficher_recapitulatif(resultats, date_reference)

    def _date_reference(self, valeur):
        aujourd_hui = timezone.localdate()
        if not valeur:
            return aujourd_hui
        try:
            date_reference = date.fromisoformat(valeur)
        except ValueError as exc:
            raise CommandError("--date-reference : format attendu AAAA-MM-JJ.") from exc
        if date_reference > aujourd_hui:
            raise CommandError("--date-reference ne peut pas être dans le futur.")
        return date_reference

    def _utilisateur(self, email):
        Utilisateur = get_user_model()
        if email:
            utilisateur = Utilisateur.objects.filter(email__iexact=email).first()
            if utilisateur is None:
                raise CommandError(f"Aucun utilisateur avec l'adresse {email}.")
        else:
            utilisateur = (
                Utilisateur.objects.filter(
                    role__libelle="Administrateur",
                    is_active=True,
                    statut="ACTIF",
                )
                .order_by("id")
                .first()
            )
            if utilisateur is None:
                raise CommandError(
                    "Aucun Administrateur actif : créez-en un ou indiquez --utilisateur."
                )
        if not utilisateur.is_active or getattr(utilisateur, "statut", None) != "ACTIF":
            raise CommandError(f"L'utilisateur {utilisateur} n'est pas actif.")
        return utilisateur

    # --- Plan (--dry-run) ----------------------------------------------------

    def _afficher_plan(self, scenarios):
        entetes = ("Scénario", "Bassin", "Entrée", "Lots", "Pesées", "Distrib.", "Provende kg", "Mortalités", "Morts", "Paramètres")
        lignes = []
        for scenario in scenarios:
            stats = scenario.statistiques()
            lignes.append((
                scenario.code,
                f"{PREFIXE}{scenario.code}",
                str(scenario.date_entree),
                " + ".join(
                    f"{lot.profil.espece} {lot.profil.effectif_initial}×{lot.profil.poids_initial} g"
                    for lot in scenario.lots
                ),
                str(stats["pesees"]),
                str(stats["distributions"]),
                f"{stats['provende_kg']:.2f}",
                str(stats["mortalites"]),
                str(stats["poissons_morts"]),
                str(stats["parametres"]),
            ))
        self._tableau(entetes, lignes)
        for scenario in scenarios:
            self.stdout.write(f"\n{scenario.code} : {scenario.definition.description}")
            for lot in scenario.lots:
                pesees = ", ".join(
                    f"J{p.jour_de_cycle} {p.poids_moyen_g} g ({p.nombre_poissons_peses}/{p.effectif_vivant})"
                    for p in lot.pesees
                )
                self.stdout.write(f"  Lot {lot.profil.cle} ({lot.profil.espece}) : {pesees or 'aucune pesée'}")

    def _tableau(self, entetes, lignes):
        largeurs = [max(len(str(x)) for x in colonne) for colonne in zip(entetes, *lignes)]
        formater = lambda valeurs: "  ".join(str(v).ljust(l) for v, l in zip(valeurs, largeurs))
        self.stdout.write(formater(entetes))
        self.stdout.write("  ".join("-" * l for l in largeurs))
        for ligne in lignes:
            self.stdout.write(formater(ligne))

    # --- Écriture --------------------------------------------------------------

    def _executer(self, client, scenario):
        from apps.bassins.models import Bassin

        reference = f"{PREFIXE}{scenario.code}"
        if Bassin.objects.filter(reference=reference).exists():
            self.stdout.write(self.style.WARNING(f"{scenario.code} : bassin {reference} déjà présent, scénario ignoré."))
            return None
        self.stdout.write(f"{scenario.code} : écriture en cours…")
        with transaction.atomic():
            lots = self._ecrire(client, scenario, reference)
        self.stdout.write(self.style.SUCCESS(f"{scenario.code} : terminé."))
        return scenario, reference, lots

    def _ecrire(self, client, scenario, reference):
        from apps.effectifs.models import CauseMortalite
        from apps.grossissement.models import LotGrossissement
        from apps.provende.models import TypeProvende

        code = scenario.code
        entree = scenario.date_entree
        bassin_gro = client.post("/api/v1/bassins/", {
            "reference": reference, "categorie": "GROSSISSEMENT", "capacity": 100000, "statut": "ACTIF",
        })["id"]
        bassin_stk = client.post("/api/v1/bassins/", {
            "reference": f"{reference}-STK", "categorie": "STOCKS", "capacity": 100000, "statut": "ACTIF",
        })["id"]

        # Une reproduction active par bassin et par espèce (la ponte la retrouve par le bassin).
        bassins_rep = {}
        for espece in sorted({lot.profil.espece for lot in scenario.lots}):
            bassin_rep = client.post("/api/v1/bassins/", {
                "reference": f"{reference}-REP-{espece}", "categorie": "REPRODUCTION",
                "capacity": 100000, "statut": "ACTIF",
            })["id"]
            client.post("/api/v1/reproduction/reproductions/", {
                "reference": f"{reference}-REPRO-{espece}", "bassin": bassin_rep, "espece": espece,
                "date_ajout": _iso(entree - timedelta(days=20)), "quantite_male": 5, "quantite_femelle": 5,
            })
            bassins_rep[espece] = bassin_rep

        type_croissance = TypeProvende.objects.filter(type="CROISSANCE").values_list("id", flat=True).first()
        if type_croissance is None:
            raise EchecSimulation("Type de provende CROISSANCE introuvable.")
        provende = client.post("/api/v1/provende/", {
            "type_provende": type_croissance, "reference": f"{reference}-PROV",
            "date_creation": _iso(entree - timedelta(days=30)), "remarques": OBSERVATION,
        })["id"]

        # Entrée des lots par le chemin officiel : … → mise en stock → sortie validée.
        lots_gross = {}
        for lot in scenario.lots:
            profil = lot.profil
            suffixe = f"{reference}-{profil.cle}"
            hapa = client.post("/api/v1/bassins/hapas/", {
                "bassin": bassin_stk, "nom": f"{suffixe}-HAPA", "actif": True,
            })["id"]
            client.post("/api/v1/reproduction/pontes/", {
                "reference": f"{suffixe}-PONTE", "bassin": bassins_rep[profil.espece],
                "date_ponte": _iso(entree - timedelta(days=19)),
                "quantite_oeuf_obtenu": str(profil.effectif_initial * 5),
            })
            eclosion = client.post("/api/v1/ecloserie/", {
                "reference": f"{suffixe}-ECLOSION", "reference_ponte": f"{suffixe}-PONTE",
                "date_eclosion": _iso(entree - timedelta(days=18)),
                "quantite_oeufs_eclos": str(profil.effectif_initial * 4),
                "quantite_alevins_obtenu": profil.effectif_initial,
            })["id"]
            lot_origine = client.post("/api/v1/lots/", {
                "reference": f"{suffixe}-LOT", "id_eclosion": eclosion,
                "date_creation": _iso(_a_heure(entree - timedelta(days=17))),
                "parcours_production": "SANS_TRAITEMENT",
            })["id"]
            client.post("/api/v1/stocks/", {
                "id_lot": lot_origine, "id_bassin": bassin_stk, "finalite": "GROSSISSEMENT",
                "date_mise_en_stock": _iso(_a_heure(entree - timedelta(days=10))),
                "hapas": [{"id_hapa": hapa, "effectif_entrant": profil.effectif_initial}],
            })
            sortie = client.post("/api/v1/effectifs/sorties-stock/", {
                "id_lot": lot_origine, "id_bassin_destination": bassin_gro,
                "date_sortie": _iso(_a_heure(entree)), "observation": OBSERVATION,
            })["id"]
            client.post(f"/api/v1/effectifs/sorties-stock/{sortie}/valider/", {
                "poids_initial_g": str(profil.poids_initial),
            })
            lots_gross[profil.cle] = LotGrossissement.objects.get(lot_origine_id=lot_origine)

        causes = dict(CauseMortalite.objects.values_list("libelle", "id"))
        if CAUSE_PAR_DEFAUT not in causes:
            raise EchecSimulation(f"Cause de mortalité « {CAUSE_PAR_DEFAUT} » introuvable.")

        # Journaux sur toute la période, avant les pesées.
        for lot in scenario.lots:
            for mortalite in lot.mortalites:
                client.post("/api/v1/effectifs/evenements-mortalite-perte/", {
                    "type_evenement": "MORTALITE", "id_bassin": bassin_gro,
                    "espece": lot.profil.espece, "quantite": mortalite.quantite,
                    "date_evenement": _iso(mortalite.date),
                    "id_cause": causes.get(mortalite.cause, causes[CAUSE_PAR_DEFAUT]),
                    "observation": OBSERVATION,
                })
        for distribution in scenario.distributions:
            client.post("/api/v1/provende/consommations-provende/", {
                "id_bassin": bassin_gro, "id_provende": provende,
                "date_distribution": _iso(distribution.date_heure),
                "quantite_kg": str(distribution.quantite_kg), "observation": OBSERVATION,
            })
        for parametre in scenario.parametres:
            client.post("/api/v1/environnement/", {
                "bassin": bassin_gro,
                "date_heure_prelevement": _iso(parametre.date_heure),
                "temperature_eau": str(parametre.temperature_eau),
                "temperature_air": str(parametre.temperature_air),
                "oxygene": str(parametre.oxygene),
                "ph": str(parametre.ph),
                "nitrite": str(parametre.nitrite),
                "nitrate": str(parametre.nitrate),
                "ammoniac": str(parametre.ammoniac),
            })

        # Pesées dans l'ordre chronologique ; les lots pesés le même jour
        # partagent un même événement de pesée.
        for jour in scenario.dates_pesee():
            lignes = [
                {
                    "id_lot_grossissement": lots_gross[lot.profil.cle].pk,
                    "pourcentage_echantillon": "10",
                    "nombre_poissons_peses": pesee.nombre_poissons_peses,
                    "poids_total_g": str(pesee.poids_total_g),
                }
                for lot in scenario.lots
                for pesee in lot.pesees
                if pesee.date == jour
            ]
            client.post("/api/v1/grossissement/pesees/", {
                "id_bassin": bassin_gro, "date": _iso(jour),
                "observation": OBSERVATION, "lignes": lignes,
            })
        return lots_gross

    # --- Récapitulatif ---------------------------------------------------------

    def _afficher_recapitulatif(self, resultats, date_reference):
        from apps.effectifs.calculs import effectif_vivant
        from apps.grossissement.models import Pesee, PeseeLot

        if not resultats:
            self.stdout.write("Aucun scénario écrit.")
            return
        entetes = ("Scénario", "Bassin", "Lot(s)", "Pesées", "Dernier poids moyen", "Dernier IC période", "Effectif vivant final")
        lignes = []
        for scenario, reference, lots in resultats:
            poids, ics, effectifs = [], [], []
            for lot in lots.values():
                derniere = (
                    PeseeLot.objects.filter(lot_grossissement=lot)
                    .select_related("pesee", "indicateur")
                    .order_by("-pesee__date")
                    .first()
                )
                indicateur = getattr(derniere, "indicateur", None) if derniere else None
                poids.append(f"{derniere.poids_moyen} g" if derniere else "-")
                ics.append(
                    str(indicateur.ic_periode)
                    if indicateur is not None and indicateur.ic_periode is not None
                    else "-"
                )
                effectifs.append(str(effectif_vivant(lot.bassin_id, lot.espece, a_la_date=date_reference)))
            bassin_id = next(iter(lots.values())).bassin_id
            lignes.append((
                scenario.code,
                reference,
                " / ".join(lot.code for lot in lots.values()),
                str(Pesee.objects.filter(bassin_id=bassin_id).count()),
                " / ".join(poids),
                " / ".join(ics),
                " / ".join(effectifs),
            ))
        self.stdout.write("")
        self._tableau(entetes, lignes)
