"""Génère la collection Postman « flux complet » (context/CONCEPTION_POSTMAN_FLUX_COMPLET.md).

Le scénario est décrit en données (`Etape`) : chaque étape porte le rôle qui
l'exécute dans l'exploitation, la requête, le code HTTP attendu et les
identifiants à retenir. Le même scénario est rejoué par le test Django
apps/core/tests/test_collection_flux_complet.py.

Régénérer : py tests/postman/generer_flux_complet.py
"""
import json
import pathlib
from dataclasses import dataclass, field
from datetime import date, timedelta


SORTIE = pathlib.Path(__file__).with_name("flux_complet.postman_collection.json")
SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"

# Variable fc_run tirée à chaque exécution : références uniques, collection rejouable.
SCRIPT_RUN = [
    "const t = new Date();",
    "const pad = (n) => String(n).padStart(2, '0');",
    "pm.collectionVariables.set('fc_run', `FC-${t.getUTCFullYear()}${pad(t.getUTCMonth() + 1)}${pad(t.getUTCDate())}"
    "-${pad(t.getUTCHours())}${pad(t.getUTCMinutes())}${pad(t.getUTCSeconds())}"
    "-${Math.random().toString(36).slice(2, 6).toUpperCase()}`);",
]
# Chemin pointé : "id", "0.id", "[type=CROISSANCE].id" (élément d'une liste par valeur).
LIRE = (
    "const lire = (o, c) => c.split('.').reduce((v, k) => {"
    " if (v === undefined || v === null) return undefined;"
    " const m = k.match(/^\[(\w+)=(.*)\]$/);"
    " if (m) return (Array.isArray(v) ? v : (v.results || [])).find(x => String(x[m[1]]) === m[2]);"
    " return v[k]; }, o);"
)


def lire(donnees, chemin):
    """Même règle que LIRE (JS), pour le rejoueur Django."""
    valeur = donnees
    for cle in chemin.split("."):
        if valeur is None:
            return None
        if cle.startswith("[") and cle.endswith("]"):
            champ, attendu = cle[1:-1].split("=", 1)
            liste = valeur if isinstance(valeur, list) else valeur.get("results", [])
            valeur = next((x for x in liste if str(x.get(champ)) == attendu), None)
        elif isinstance(valeur, list):
            index = int(cle)
            valeur = valeur[index] if index < len(valeur) else None
        else:
            valeur = valeur.get(cle)
    return valeur


def texte(valeur):
    """String(valeur) en JS : les vérifications comparent des textes, à l'identique des deux côtés."""
    if valeur is None:
        return "undefined"
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    if isinstance(valeur, float) and valeur.is_integer():
        return str(int(valeur))
    return str(valeur)


@dataclass
class Etape:
    nom: str
    role: str | None  # "directeur" | "gestionnaire" | "employe" | "admin" | None (sans jeton)
    methode: str
    chemin: str  # commence par /api/ ; peut contenir des {{fc_…}}
    corps: dict | None = None
    attendu: int = 200
    capture: dict = field(default_factory=dict)  # variable fc_… -> champ (chemin pointé)
    verifs: list = field(default_factory=list)  # (nom, chemin pointé, valeur attendue)
    refus: bool = False
    avant: list = field(default_factory=list)  # script de pré-requête


def refus(nom, role, methode, chemin, corps=None, attendu=403):
    """Étape qui doit être refusée sans rien écrire."""
    return Etape(nom, role, methode, chemin, corps if corps is not None else {}, attendu, refus=True)


def connexions():
    comptes = (
        ("admin", "admin", "Connexion administrateur (support)"),
        ("directeur", "director", "Connexion directeur"),
        ("gestionnaire", "manager", "Connexion gestionnaire (gérante)"),
        ("employe", "employee", "Connexion employé"),
    )
    etapes = []
    for role, cle, nom in comptes:
        etapes.append(Etape(
            nom, None, "POST", "/api/auth/login/",
            {"username": "{{" + cle + "_username}}", "password": "{{" + cle + "_password}}"},
            capture={f"fc_token_{role}": "access"},
            avant=SCRIPT_RUN if role == "admin" else [],
        ))
    return etapes


# --- Briques du scénario -----------------------------------------------------------

def verif(nom, chemin, attendu):
    """Contrôle d'un champ de la réponse (chemin pointé, comme les captures)."""
    return (nom, chemin, attendu)


def instant(jour, heure="08:00"):
    """Date-heure locale (Indian/Antananarivo) au format ISO."""
    return f"{jour.isoformat()}T{heure}:00+03:00"


def plus(jour, jours):
    return jour + timedelta(days=jours)


def ref(texte):
    return f"FC-{texte}-{{{{fc_run}}}}"


def bassin(cle, categorie):
    return Etape(
        f"Directeur : créer le bassin {cle}", "directeur", "POST", "/api/v1/bassins/",
        {"reference": ref(cle), "categorie": categorie, "capacity": 100000, "statut": "ACTIF"},
        attendu=201, capture={f"fc_bassin_{cle.lower().replace('-', '_')}": "id"},
    )


def hapa(cle_bassin, nom):
    return Etape(
        f"Directeur : créer le hapa {nom}", "directeur", "POST", "/api/v1/bassins/hapas/",
        {"bassin": "{{fc_bassin_" + cle_bassin + "}}", "nom": ref(nom), "actif": True},
        attendu=201, capture={f"fc_hapa_{nom.lower().replace('-', '_')}": "id"},
    )


def naissance(lot, espece, alevins, debut, parcours="TRAITEMENT_REQUIS"):
    """Reproduction → ponte → éclosion → lot (directeur), dans un bassin de reproduction dédié."""
    b = f"rep_{lot}"
    return [
        bassin(f"REP-{lot.upper()}", "REPRODUCTION"),
        Etape(f"Directeur : reproduction {espece} du lot {lot.upper()}", "directeur", "POST",
              "/api/v1/reproduction/reproductions/",
              {"reference": ref(f"REPRO-{lot.upper()}"), "bassin": "{{fc_bassin_" + b + "}}", "espece": espece,
               "date_ajout": debut.isoformat(), "quantite_male": 10, "quantite_femelle": 10},
              attendu=201, capture={f"fc_repro_{lot}": "id"}),
        Etape(f"Directeur : ponte du lot {lot.upper()}", "directeur", "POST", "/api/v1/reproduction/pontes/",
              {"reference": ref(f"PONTE-{lot.upper()}"), "bassin": "{{fc_bassin_" + b + "}}",
               "date_ponte": plus(debut, 1).isoformat(), "quantite_oeuf_obtenu": f"{alevins * 3}.00"},
              attendu=201),
        Etape(f"Directeur : éclosion du lot {lot.upper()}", "directeur", "POST", "/api/v1/ecloserie/",
              {"reference": ref(f"ECLOSION-{lot.upper()}"), "reference_ponte": ref(f"PONTE-{lot.upper()}"),
               "date_eclosion": plus(debut, 2).isoformat(), "quantite_oeufs_eclos": f"{alevins * 2}.00",
               "quantite_alevins_obtenu": alevins},
              attendu=201, capture={f"fc_eclosion_{lot}": "id"}),
        Etape(f"Directeur : créer le lot {lot.upper()}", "directeur", "POST", "/api/v1/lots/",
              {"reference": ref(f"LOT-{lot.upper()}"), "id_eclosion": "{{fc_eclosion_" + lot + "}}",
               "date_creation": instant(plus(debut, 3), "12:00"), "parcours_production": parcours},
              attendu=201, capture={f"fc_lot_{lot}": "id"},
              verifs=[verif("Lot créé au stade CREEE", "stade", "CREEE")]),
    ]


def mortalite(nom, role, bassin_cle, espece, quantite, jour, hapa_cle=None, attendu=201, capture=None):
    corps = {"type_evenement": "MORTALITE", "id_bassin": "{{fc_bassin_" + bassin_cle + "}}", "espece": espece,
             "quantite": quantite, "date_evenement": instant(jour), "id_cause": "{{fc_cause_inconnue}}"}
    if hapa_cle:
        corps["id_hapa"] = "{{fc_hapa_" + hapa_cle + "}}"
    return Etape(nom, role, "POST", "/api/v1/effectifs/evenements-mortalite-perte/", corps,
                 attendu=attendu, capture=capture or {})


def traitement(lot, espece, effectif, hapa_cle, ouverture, morts, cloture=None, apercu=True):
    """Ouverture (directeur), mortalités (employé), aperçu (employé), clôture datée (directeur)."""
    etapes = [
        Etape(f"Directeur : ouvrir le traitement du lot {lot.upper()}", "directeur", "POST", "/api/v1/traitement/",
              {"id_lot": "{{fc_lot_" + lot + "}}", "id_bassin": "{{fc_bassin_trt}}",
               "id_used_provende": "{{fc_provende_trt}}", "date_ouverture": ouverture.isoformat(),
               "effectif_initial": effectif, "hapas": [{"id_hapa": "{{fc_hapa_" + hapa_cle + "}}", "effectif_initial": effectif}]},
              attendu=201, capture={f"fc_trt_{lot}": "id"},
              verifs=[verif("Traitement en cours", "statut", "EN_COURS")]),
    ]
    for rang, (quantite, jours) in enumerate(morts, start=1):
        etapes.append(mortalite(f"Employé : mortalité n°{rang} en traitement ({lot.upper()})", "employe", "trt",
                                espece, quantite, plus(ouverture, jours), hapa_cle=hapa_cle))
    if cloture is None:
        return etapes
    final = effectif - sum(q for q, _ in morts)
    parametre = "" if cloture == "aujourdhui" else f"?date_cloture={cloture.isoformat()}"
    corps = {} if cloture == "aujourdhui" else {"date_cloture": cloture.isoformat()}
    if apercu:
        etapes.append(Etape(f"Employé : aperçu de la clôture du traitement {lot.upper()} (boîte de validation)", "employe",
                            "GET", "/api/v1/traitement/{{fc_trt_" + lot + "}}/apercu-cloture/" + parametre,
                            verifs=[verif("Clôturable", "cloturable", True),
                                    verif("Effectif final lu dans le registre", "effectif_final", final)]))
    etapes.append(Etape(f"Directeur : clôturer le traitement du lot {lot.upper()}", "directeur", "POST",
                        "/api/v1/traitement/{{fc_trt_" + lot + "}}/cloturer/", corps,
                        verifs=[verif("Traitement clôturé", "statut", "CLOTUREE"),
                                verif("Effectif final", "effectif_final", final),
                                verif("Mise en stock proposée", "mise_en_stock_possible", True)]))
    return etapes


def mise_en_stock(lot, hapa_cle, effectif, jour=None):
    corps = {"id_lot": "{{fc_lot_" + lot + "}}", "id_bassin": "{{fc_bassin_stk}}", "finalite": "GROSSISSEMENT",
             "hapas": [{"id_hapa": "{{fc_hapa_" + hapa_cle + "}}", "effectif_entrant": effectif}]}
    if jour is not None:
        corps["date_mise_en_stock"] = instant(jour, "09:00")
    return Etape(f"Directeur : mettre le lot {lot.upper()} en stock", "directeur", "POST", "/api/v1/stocks/", corps,
                 attendu=201, capture={f"fc_stock_{lot}": "id"},
                 verifs=[verif("Effectif entrant", "effectif_entrant", effectif)])


def sortie(lot, bassin_cle, effectif, jour, poids_g):
    nombre = -(-effectif // 10)  # cible : 10 % arrondi au supérieur
    corps = {"id_lot": "{{fc_lot_" + lot + "}}", "id_bassin_destination": "{{fc_bassin_" + bassin_cle + "}}",
             "date_pesee_initiale": jour.isoformat(), "pourcentage_echantillon": "10",
             "nombre_poissons_peses": nombre, "poids_total_g": f"{poids_g * nombre}.00",
             "date_mise_en_place": jour.isoformat(), "effectif_entrant": effectif}
    return [
        Etape(f"Directeur : aperçu de la sortie du lot {lot.upper()} vers le grossissement", "directeur", "POST",
              "/api/v1/grossissement/lots-grossissement/apercu-sortie/", corps,
              verifs=[verif("Quantité sortie", "quantite_sortie", effectif),
                      verif("Échantillon conforme", "echantillon_conforme", True)]),
        Etape(f"Directeur : sortie du lot {lot.upper()} et création du lot de grossissement", "directeur", "POST",
              "/api/v1/grossissement/lots-grossissement/", corps, attendu=201, capture={f"fc_lgr_{lot}": "id"},
              verifs=[verif("Lot de grossissement actif", "statut", "ACTIF"),
                      verif("Effectif initial", "effectif_initial", effectif),
                      verif("Poids moyen initial (P0)", "poids_moyen_initial", f"{poids_g}.00")]),
    ]


def pesee(lot, jour, effectif, poids_g, capture=None):
    """Échantillon de 10 % de l'effectif d'entrée : toujours au moins la cible (10 % des vivants)."""
    nombre = -(-effectif // 10)
    return Etape(f"Employé : pesée du lot {lot.upper()} le {jour.isoformat()} ({poids_g} g)", "employe", "POST",
                 "/api/v1/grossissement/pesees/",
                 {"id_lot_grossissement": "{{fc_lgr_" + lot + "}}", "date": jour.isoformat(),
                  "nombre_poissons_peses": nombre, "poids_total_g": f"{poids_g * nombre}.00"},
                 attendu=201, capture=capture or {},
                 verifs=[verif("Poids moyen", "poids_moyen", f"{poids_g}.00")])


def distributions(bassin_cle, debut, fin, pas, kg_depart, kg_pas):
    etapes, jour, kg = [], debut, kg_depart
    while jour <= fin:
        etapes.append(Etape(f"Employé : provende du {jour.isoformat()} ({bassin_cle})", "employe", "POST",
                            "/api/v1/provende/consommations-provende/",
                            {"id_bassin": "{{fc_bassin_" + bassin_cle + "}}", "id_provende": "{{fc_provende_croissance}}",
                             "date_distribution": instant(jour, "07:00"), "quantite_kg": f"{kg:.2f}"},
                            attendu=201))
        jour, kg = plus(jour, pas), kg + kg_pas
    return etapes


def environnement(bassin_cle, jours):
    return [
        Etape(f"Employé : paramètres environnementaux du {jour.isoformat()} ({bassin_cle})", "employe", "POST",
              "/api/v1/environnement/",
              {"bassin": "{{fc_bassin_" + bassin_cle + "}}", "date_heure_prelevement": instant(jour, "06:30"),
               "temperature_eau": "27.50", "temperature_air": "29.00", "oxygene": "6.20", "ph": "7.20",
               "nitrite": "0.05", "nitrate": "1.10", "ammoniac": "0.02"},
              attendu=201)
        for jour in jours
    ]


# --- Scénario ----------------------------------------------------------------------

D = date  # dates fixes de 2026
CREE = "/api/v1/lots/"


def construire_dossiers():
    """Le scénario complet, dossier par dossier (section 5 de la conception)."""
    # Lot P : TILAPIA, traité en janvier, grossissement de février au seuil, clôturé aujourd'hui.
    p_entree = D(2026, 2, 1)
    p_effectif = 2000 - 30          # 30 morts en traitement
    poids_p = [45, 80, 105, 130, 155, 180, 205, 230, 255]
    jours_p = [30, 60, 75, 90, 105, 120, 135, 150, 165]
    # G1 (CARPE) et G2 (TILAPIA) : traités en juin-juillet, entrés le 1er août, pesés jusqu'à J75.
    g_entree = D(2026, 8, 1)
    g1_effectif, g2_effectif = 1000 - 20, 800 - 15

    cycle_p = sortie("p", "gro_p", p_effectif, p_entree, 20)
    cycle_p += environnement("gro_p", [D(2026, 2, 15), D(2026, 4, 15), D(2026, 6, 15), D(2026, 9, 15)])
    cycle_p += distributions("gro_p", D(2026, 2, 8), D(2026, 11, 8), 14, 8.0, 1.5)
    cycle_p += [
        mortalite("Employé : mortalité saisie par erreur (lot P)", "employe", "gro_p", "TILAPIA", 50,
                  D(2026, 2, 20), capture={"fc_mortalite_erreur": "id"}),
        refus("Employé : annulation d'une mortalité refusée", "employe", "POST",
              "/api/v1/effectifs/evenements-mortalite-perte/{{fc_mortalite_erreur}}/annuler/", {"motif": "Erreur"}),
        Etape("Directeur : annuler la mortalité saisie par erreur", "directeur", "POST",
              "/api/v1/effectifs/evenements-mortalite-perte/{{fc_mortalite_erreur}}/annuler/",
              {"motif": "Erreur de saisie : 5 poissons et non 50"}, attendu=201,
              verifs=[verif("Mortalité annulée", "statut", "ANNULEE")]),
        mortalite("Employé : ressaisie de la mortalité correcte (lot P)", "employe", "gro_p", "TILAPIA", 5, D(2026, 2, 20)),
        mortalite("Employé : mortalité (lot P)", "employe", "gro_p", "TILAPIA", 12, D(2026, 4, 10)),
        mortalite("Employé : mortalité (lot P)", "employe", "gro_p", "TILAPIA", 8, D(2026, 6, 20)),
        refus("Gérante : validation d'une sortie refusée", "gestionnaire", "POST",
              "/api/v1/grossissement/lots-grossissement/", {}),
    ]
    for rang, (jours, poids) in enumerate(zip(jours_p, poids_p)):
        cycle_p.append(pesee("p", plus(p_entree, jours), p_effectif, poids,
                             capture={"fc_pesee_p1": "id"} if rang == 0 else None))
    cycle_p += [
        refus("Employé : correction d'une pesée refusée", "employe", "PATCH",
              "/api/v1/grossissement/pesees/{{fc_pesee_p1}}/", {"poids_total_g": "1.00"}),
        refus("Gérante : saisie d'une pesée refusée", "gestionnaire", "POST", "/api/v1/grossissement/pesees/", {}),
        pesee("p", D(2026, 11, 10), p_effectif, 320),
    ]

    g_cycle = sortie("g1", "gro_g", g1_effectif, g_entree, 15) + sortie("g2", "gro_g", g2_effectif, g_entree, 12)
    g_cycle += environnement("gro_g", [D(2026, 8, 10), D(2026, 9, 10), D(2026, 10, 10)])
    g_cycle += distributions("gro_g", D(2026, 8, 8), D(2026, 11, 14), 7, 6.0, 0.8)
    g_cycle += [
        mortalite("Employé : mortalité CARPE (G1)", "employe", "gro_g", "CARPE", 6, D(2026, 8, 20)),
        mortalite("Employé : mortalité TILAPIA (G2)", "employe", "gro_g", "TILAPIA", 4, D(2026, 9, 5)),
    ]
    for jours, (poids1, poids2) in zip((30, 60, 75), ((32, 28), (55, 50), (70, 64))):
        g_cycle += [pesee("g1", plus(g_entree, jours), g1_effectif, poids1),
                    pesee("g2", plus(g_entree, jours), g2_effectif, poids2)]
    g_cycle += [
        Etape("Gérante : suivi de G1 (alerte de pesée en retard)", "gestionnaire", "GET",
              "/api/v1/grossissement/lots-grossissement/{{fc_lgr_g1}}/suivi/",
              verifs=[verif("Trois pesées", "nombre_pesees", 3),
                      verif("Prochaine pesée J90", "calendrier.prochaine_pesee.date_prevue", "2026-10-30"),
                      verif("En retard", "calendrier.prochaine_pesee.en_retard", True),
                      verif("Alerte PESEE_EN_RETARD", "alertes.0.code", "PESEE_EN_RETARD"),
                      verif("Autre lot du bassin (G2)", "autres_lots_du_bassin.0.id", "{{fc_lgr_g2}}")]),
        Etape("Gérante : fiche de G2", "gestionnaire", "GET",
              "/api/v1/grossissement/lots-grossissement/{{fc_lgr_g2}}/",
              verifs=[verif("Lot actif", "statut", "ACTIF"), verif("Pesée en retard", "pesee_en_retard", True)]),
    ]

    return [
        ("00 Connexions", connexions()),
        ("01 Comptes et accès", [
            Etape("Gérante : lire son compte", "gestionnaire", "GET", "/api/auth/me/", capture={"fc_gerante_id": "id"}),
            Etape("Gérante : lire les rôles", "gestionnaire", "GET", "/api/roles/",
                  capture={"fc_role_admin": "[libelle=Administrateur].id"}),
            refus("Gérante : s'attribuer le rôle Administrateur (refusé)", "gestionnaire", "PATCH",
                  "/api/users/{{fc_gerante_id}}/", {"role_id": "{{fc_role_admin}}"}),
            refus("Sans jeton : accès refusé", None, "GET", "/api/v1/lots/", attendu=401),
        ]),
        ("02 Référentiels", [
            Etape("Directeur : lire les types de provende", "directeur", "GET", "/api/v1/provende/types/",
                  capture={"fc_type_trt": "[type=TRAITEMENT].id", "fc_type_croissance": "[type=CROISSANCE].id"}),
            Etape("Employé : lire les causes de mortalité", "employe", "GET", "/api/v1/effectifs/causes-mortalite/?actif=true",
                  capture={"fc_cause_inconnue": "[libelle=Inconnue].id"}),
            Etape("Directeur : provende de traitement", "directeur", "POST", "/api/v1/provende/",
                  {"type_provende": "{{fc_type_trt}}", "reference": ref("PROV-TRT"), "date_creation": "2025-12-01"},
                  attendu=201, capture={"fc_provende_trt": "id"}),
            Etape("Directeur : provende de croissance", "directeur", "POST", "/api/v1/provende/",
                  {"type_provende": "{{fc_type_croissance}}", "reference": ref("PROV-CROISSANCE"), "date_creation": "2025-12-01"},
                  attendu=201, capture={"fc_provende_croissance": "id"}),
            bassin("TRT", "TRAITEMENT"), bassin("STK", "STOCKS"), bassin("GRO-P", "GROSSISSEMENT"), bassin("GRO-G", "GROSSISSEMENT"),
            *[hapa("trt", f"TRT-{lot.upper()}") for lot in ("p", "t1", "t2", "g1", "g2")],
            *[hapa("stk", f"STK-{lot.upper()}") for lot in ("p", "t2", "g1", "g2")],
            refus("Gérante : création d'un bassin refusée", "gestionnaire", "POST", "/api/v1/bassins/", {}),
        ]),
        ("03 Lot P : naissance, traitement et stock", [
            *naissance("p", "TILAPIA", 2000, D(2026, 1, 1)),
            refus("Employé : création d'un lot refusée", "employe", "POST", CREE, {}),
            refus("Gérante : ouverture d'un traitement refusée", "gestionnaire", "POST", "/api/v1/traitement/", {}),
            *traitement("p", "TILAPIA", 2000, "trt_p", D(2026, 1, 5), [(18, 3), (12, 7)], cloture=D(2026, 1, 15)),
            refus("Gérante : mise en stock refusée", "gestionnaire", "POST", "/api/v1/stocks/", {}),
            mise_en_stock("p", "stk_p", p_effectif, D(2026, 1, 16)),
        ]),
        ("04 Lot P : sortie et cycle de grossissement", cycle_p),
        ("05 Lot P : clôture et évacuation", [
            Etape("Directeur : aperçu de la clôture du lot P", "directeur", "GET",
                  "/api/v1/grossissement/lots-grossissement/{{fc_lgr_p}}/apercu-cloture/",
                  verifs=[verif("Clôturable", "cloturable", True), verif("Poids final = dernière pesée", "poids_moyen_final", 320.0)]),
            refus("Employé : clôture refusée", "employe", "POST",
                  "/api/v1/grossissement/lots-grossissement/{{fc_lgr_p}}/cloturer/", {}),
            Etape("Directeur : clôturer le cycle du lot P", "directeur", "POST",
                  "/api/v1/grossissement/lots-grossissement/{{fc_lgr_p}}/cloturer/", {"observation": "Fin de cycle"},
                  verifs=[verif("Lot clôturé", "statut", "CLOTURE"), verif("Évacuation à proposer", "evacuation_requise", True)]),
            refus("Gérante : évacuation refusée", "gestionnaire", "POST",
                  "/api/v1/grossissement/lots-grossissement/{{fc_lgr_p}}/evacuer/", {}),
            Etape("Directeur : évacuer le bassin du lot P", "directeur", "POST",
                  "/api/v1/grossissement/lots-grossissement/{{fc_lgr_p}}/evacuer/", {},
                  verifs=[verif("Bassin évacué", "evacuation_requise", False)]),
            Etape("Gérante : historique des clôtures du bassin", "gestionnaire", "GET",
                  "/api/v1/grossissement/lots-grossissement/historique-clotures/?bassin={{fc_bassin_gro_p}}",
                  verifs=[verif("Une clôture", "0.id", "{{fc_lgr_p}}"), verif("Évacué", "0.evacue", True)]),
            Etape("Gérante : suivi du lot P clôturé (lecture seule)", "gestionnaire", "GET",
                  "/api/v1/grossissement/lots-grossissement/{{fc_lgr_p}}/suivi/",
                  verifs=[verif("Cycle clôturé", "lot.statut", "CLOTURE"), verif("Dix pesées", "nombre_pesees", 10)]),
            Etape("Gérante : lot d'origine P clôturé", "gestionnaire", "GET", "/api/v1/lots/{{fc_lot_p}}/",
                  verifs=[verif("Stade CLOTUREE", "stade", "CLOTUREE")]),
        ]),
        ("06 Lots T1 et T2 : traitement", [
            *naissance("t1", "CARPE", 600, D(2026, 10, 25)),
            *traitement("t1", "CARPE", 600, "trt_t1", D(2026, 11, 1), [(4, 4)]),
            *naissance("t2", "CARPE", 500, D(2026, 10, 10)),
            *traitement("t2", "CARPE", 500, "trt_t2", D(2026, 10, 20), [(6, 5), (3, 12)], cloture="aujourdhui"),
            refus("Employé : clôture d'un traitement refusée", "employe", "POST", "/api/v1/traitement/{{fc_trt_t1}}/cloturer/", {}),
            mise_en_stock("t2", "stk_t2", 500 - 9),
            Etape("Gérante : traitement T1 toujours en cours", "gestionnaire", "GET", "/api/v1/traitement/{{fc_trt_t1}}/",
                  verifs=[verif("En cours", "statut", "EN_COURS")]),
            Etape("Gérante : lot T2 en stock", "gestionnaire", "GET", "/api/v1/lots/{{fc_lot_t2}}/",
                  verifs=[verif("En stock", "stade", "EN_STOCK")]),
        ]),
        ("07 Lots G1 et G2 : grossissement en cours", [
            *naissance("g1", "CARPE", 1000, D(2026, 5, 20)),
            *traitement("g1", "CARPE", 1000, "trt_g1", D(2026, 6, 1), [(12, 6), (8, 20)], cloture=D(2026, 7, 5), apercu=False),
            mise_en_stock("g1", "stk_g1", g1_effectif, D(2026, 7, 10)),
            *naissance("g2", "TILAPIA", 800, D(2026, 5, 22)),
            *traitement("g2", "TILAPIA", 800, "trt_g2", D(2026, 6, 3), [(15, 9)], cloture=D(2026, 7, 5), apercu=False),
            mise_en_stock("g2", "stk_g2", g2_effectif, D(2026, 7, 10)),
            *g_cycle,
        ]),
    ]


def _script_test(etape):
    lignes = [
        "{",
        "let data; try { data = pm.response.json(); } catch (e) { data = {}; }",
        f'pm.test("Statut HTTP {etape.attendu}", function () {{ pm.response.to.have.status({etape.attendu}); }});',
    ]
    if etape.capture:
        lignes.append(LIRE)
        for variable, champ in etape.capture.items():
            lignes += [
                f"const {variable} = lire(data, {json.dumps(champ)});",
                f'pm.test("Capture {variable}", function () {{ pm.expect({variable}, "{champ}").to.not.be.undefined; pm.expect({variable}).to.not.be.null; }});',
                f'if ({variable} !== undefined && {variable} !== null) pm.collectionVariables.set("{variable}", String({variable}));',
            ]
    if etape.verifs and not etape.capture:
        lignes.append(LIRE)
    for nom, chemin, attendu in etape.verifs:
        valeur = json.dumps(attendu, ensure_ascii=False)
        if isinstance(attendu, str) and "{{" in attendu:
            valeur = "pm.variables.replaceIn(" + valeur + ")"
        lignes.append(
            f"pm.test({json.dumps(nom, ensure_ascii=False)}, function () {{ "
            f"pm.expect(String(lire(data, {json.dumps(chemin)}))).to.eql(String({valeur})); }});"
        )
    lignes.append("}")
    return lignes


def _requete(numero, etape):
    requete = {
        "method": etape.methode,
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "url": "{{base_url}}" + etape.chemin,
        "auth": (
            {"type": "noauth"}
            if etape.role is None
            else {"type": "bearer", "bearer": [{"key": "token", "value": "{{fc_token_" + etape.role + "}}", "type": "string"}]}
        ),
    }
    if etape.corps is not None and etape.methode != "GET":
        requete["body"] = {
            "mode": "raw",
            "raw": json.dumps(etape.corps, ensure_ascii=False, indent=2),
            "options": {"raw": {"language": "json"}},
        }
    evenements = [{"listen": "test", "script": {"type": "text/javascript", "exec": _script_test(etape)}}]
    if etape.avant:
        evenements.insert(0, {"listen": "prerequest", "script": {"type": "text/javascript", "exec": list(etape.avant)}})
    return {"name": f"{numero} {etape.nom}", "event": evenements, "request": requete}


def generer_collection(dossiers):
    items = []
    for dossier, etapes in dossiers:
        prefixe = dossier.split(" ", 1)[0]
        items.append({
            "name": dossier,
            "item": [_requete(f"{prefixe}.{rang:02d}", etape) for rang, etape in enumerate(etapes, start=1)],
        })
    return {
        "info": {
            "name": "Fish Farm — flux complet",
            "description": (
                "Parcours complet de l'exploitation, chaque étape jouée par le rôle réel "
                "(matrice des permissions). Généré par tests/postman/generer_flux_complet.py : "
                "ne pas modifier à la main."
            ),
            "schema": SCHEMA,
        },
        "item": items,
        "variable": [{"key": "fc_run", "value": ""}],
    }


def main():
    collection = generer_collection(construire_dossiers())
    SORTIE.write_text(json.dumps(collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = sum(len(dossier["item"]) for dossier in collection["item"])
    print(f"{SORTIE.name} : {len(collection['item'])} dossiers, {total} requêtes")


if __name__ == "__main__":
    main()
