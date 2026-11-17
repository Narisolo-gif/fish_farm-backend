"""Cahier de test Excel de la collection « flux complet » (une ligne par requête).

Généré depuis le scénario de generer_flux_complet.py : toujours aligné sur la collection.
Régénérer (efface les résultats saisis) : py tests/postman/generer_cahier_de_test.py
Dépendance : openpyxl.
"""
import importlib.util
import pathlib

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

RACINE = pathlib.Path(__file__).resolve().parents[2]
SORTIE = RACINE / "tests" / "postman" / "cahier_de_test_flux_complet.xlsx"
spec = importlib.util.spec_from_file_location("gfc", RACINE / "tests/postman/generer_flux_complet.py")
gfc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gfc)

ROLES = {"directeur": "Directeur", "gestionnaire": "Gérante (Gestionnaire)", "employe": "Employé",
         "admin": "Administrateur", None: "Sans jeton"}
RESULTATS = ["OK", "KO", "Bloqué", "Non testé"]
POLICE = "Arial"
fin = Side(style="thin", color="BFBFBF")
BORD = Border(left=fin, right=fin, top=fin, bottom=fin)
ENTETE = PatternFill("solid", start_color="1F4E78")
SAISIE = PatternFill("solid", start_color="FFF2CC")
DOSSIER = PatternFill("solid", start_color="DDEBF7")


def attendu(etape):
    lignes = [f"HTTP {etape.attendu}"]
    if etape.refus:
        lignes.append("Refus attendu : aucune donnée créée ni modifiée")
    for nom, chemin, valeur in etape.verifs:
        valeur = gfc.texte(valeur).replace("{{", "").replace("}}", "")
        lignes.append(f"{nom} : {chemin} = {valeur}")
    for variable in etape.capture:
        lignes.append(f"Identifiant retenu : {variable}")
    return "\n".join(lignes)


wb = Workbook()

# --- Feuille Cahier ---------------------------------------------------------
ws = wb.active
ws.title = "Cahier de test"
ws["A1"] = "Cahier de test — collection Postman « flux complet »"
ws["A1"].font = Font(name=POLICE, size=14, bold=True)
ws["A2"] = ("Collection tests/postman/flux_complet.postman_collection.json, à exécuter dans l'ordre. "
            "Saisir uniquement les colonnes jaunes : Résultat (liste) et Commentaire.")
ws["A2"].font = Font(name=POLICE, italic=True, size=9)
ws["A3"] = "Testeur :"
ws["C3"] = "Date d'exécution :"
ws["E3"] = "Environnement / base :"
for c in ("A3", "C3", "E3"):
    ws[c].font = Font(name=POLICE, bold=True, size=10)
for c in ("B3", "D3", "F3"):
    ws[c].fill = SAISIE
    ws[c].border = BORD
    ws[c].font = Font(name=POLICE, size=10)
ws["D3"].number_format = "dd/mm/yyyy"

entetes = ["N°", "Dossier", "Scénario / étape", "Rôle", "Requête", "Résultat attendu", "Résultat", "Commentaire"]
largeurs = [8, 26, 52, 18, 52, 62, 12, 40]
LIGNE_ENTETE = 5
for i, (titre, largeur) in enumerate(zip(entetes, largeurs), start=1):
    cellule = ws.cell(row=LIGNE_ENTETE, column=i, value=titre)
    cellule.font = Font(name=POLICE, bold=True, color="FFFFFF", size=10)
    cellule.fill = ENTETE
    cellule.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cellule.border = BORD
    ws.column_dimensions[cellule.column_letter].width = largeur

ligne = LIGNE_ENTETE + 1
premiere = ligne
for dossier, etapes in gfc.construire_dossiers():
    prefixe = dossier.split(" ", 1)[0]
    for rang, etape in enumerate(etapes, start=1):
        role = "Connexion" if etape.chemin == "/api/auth/login/" else ROLES[etape.role]
        valeurs = [f"{prefixe}.{rang:02d}", dossier, etape.nom, role,
                   f"{etape.methode} {etape.chemin}", attendu(etape), "Non testé", None]
        for col, valeur in enumerate(valeurs, start=1):
            c = ws.cell(row=ligne, column=col, value=valeur)
            c.font = Font(name=POLICE, size=9)
            c.alignment = Alignment(vertical="top", wrap_text=True)
            c.border = BORD
            if col == 2:
                c.fill = DOSSIER
            if col in (7, 8):
                c.fill = SAISIE
        if etape.refus:
            ws.cell(row=ligne, column=3).font = Font(name=POLICE, size=9, color="C00000")
        ligne += 1
derniere = ligne - 1

dv = DataValidation(type="list", formula1='"' + ",".join(RESULTATS) + '"', allow_blank=True,
                    showErrorMessage=True, errorTitle="Résultat", error="Choisir OK, KO, Bloqué ou Non testé.")
dv.add(f"G{premiere}:G{derniere}")
ws.add_data_validation(dv)
plage = f"G{premiere}:G{derniere}"
for valeur, couleur in (("OK", "C6EFCE"), ("KO", "FFC7CE"), ("Bloqué", "FFEB9C")):
    ws.conditional_formatting.add(plage, FormulaRule(formula=[f'G{premiere}="{valeur}"'],
                                                     fill=PatternFill("solid", start_color=couleur)))
ws.freeze_panes = f"D{premiere}"
ws.auto_filter.ref = f"A{LIGNE_ENTETE}:H{derniere}"
ws.page_setup.orientation = "landscape"
ws.page_setup.fitToWidth = 1
ws.page_setup.fitToHeight = 0
ws.sheet_properties.pageSetUpPr.fitToPage = True
ws.print_title_rows = f"{LIGNE_ENTETE}:{LIGNE_ENTETE}"

# --- Feuille Synthèse ---------------------------------------------------------
sy = wb.create_sheet("Synthèse")
sy["A1"] = "Synthèse des résultats"
sy["A1"].font = Font(name=POLICE, size=14, bold=True)
cahier = "'Cahier de test'"
col_dossier = f"{cahier}!$B${premiere}:$B${derniere}"
col_resultat = f"{cahier}!$G${premiere}:$G${derniere}"
titres = ["Dossier", "Requêtes", "OK", "KO", "Bloqué", "Non testé", "% OK"]
for i, t in enumerate(titres, start=1):
    c = sy.cell(row=3, column=i, value=t)
    c.font = Font(name=POLICE, bold=True, color="FFFFFF", size=10)
    c.fill = ENTETE
    c.border = BORD
    c.alignment = Alignment(horizontal="center")
r = 4
for dossier, _ in gfc.construire_dossiers():
    sy.cell(row=r, column=1, value=dossier)
    sy.cell(row=r, column=2, value=f"=COUNTIF({col_dossier},$A{r})")
    for j, valeur in enumerate(RESULTATS, start=3):
        sy.cell(row=r, column=j, value=f'=COUNTIFS({col_dossier},$A{r},{col_resultat},"{valeur}")')
    sy.cell(row=r, column=7, value=f"=IF(B{r}=0,0,C{r}/B{r})")
    r += 1
sy.cell(row=r, column=1, value="Total")
for j in range(2, 7):
    lettre = "ABCDEFG"[j - 1]
    sy.cell(row=r, column=j, value=f"=SUM({lettre}4:{lettre}{r - 1})")
sy.cell(row=r, column=7, value=f"=IF(B{r}=0,0,C{r}/B{r})")
for ligne_s in range(4, r + 1):
    for col in range(1, 8):
        c = sy.cell(row=ligne_s, column=col)
        c.font = Font(name=POLICE, size=10, bold=(ligne_s == r))
        c.border = BORD
    sy.cell(row=ligne_s, column=7).number_format = "0.0%"
sy.column_dimensions["A"].width = 44
for lettre in "BCDEFG":
    sy.column_dimensions[lettre].width = 12
sy.cell(row=r + 2, column=1, value="Statut global :").font = Font(name=POLICE, bold=True)
sy.cell(row=r + 2, column=2, value=(f'=IF(D{r}>0,"KO",IF(F{r}+E{r}>0,"En cours","Validé"))')).font = Font(name=POLICE, bold=True)

# --- Feuille Mode d'emploi -------------------------------------------------------
me = wb.create_sheet("Mode d'emploi")
lignes = [
    ("Mode d'emploi", True),
    ("Prérequis : migrate fait ; comptes admin_demo, directeur_demo, gestionnaire_demo, employe_demo au statut ACTIF (voir tests/postman/README_TEST.md).", False),
    ("Exécution : npx newman@6.1.3 run tests/postman/flux_complet.postman_collection.json -e tests/postman/pisciculture_local.postman_environment.json --env-var \"base_url=http://localhost:8000\"", False),
    ("Ou Postman Desktop : importer collection + environnement, lancer la collection entière dans l'ordre (les dossiers réutilisent les identifiants des précédents).", False),
    ("Pas besoin de vider la base : chaque exécution crée ses propres références.", False),
    ("", False),
    ("Colonnes à remplir (fond jaune) : Résultat et Commentaire ; en-tête : testeur, date, environnement.", True),
    ("OK : code HTTP et toutes les vérifications de « Résultat attendu » conformes (assertions vertes dans newman/Postman).", False),
    ("KO : code HTTP ou une vérification différente → décrire l'écart en commentaire (code reçu, message d'erreur).", False),
    ("Bloqué : non exécutable à cause d'un échec précédent (identifiant manquant) → indiquer le N° en cause.", False),
    ("Non testé : valeur par défaut.", False),
    ("Les étapes en rouge sont des refus attendus (403 ou 401) : elles sont OK quand l'accès est refusé.", False),
    ("", False),
    ("Exemple de ligne remplie :", True),
    ("N° 03.07 · Résultat : KO · Commentaire : reçu 400 {\"date_cloture\": ...} au lieu de 200 ; bloque 03.08 à 03.10.", False),
]
for i, (texte, gras) in enumerate(lignes, start=1):
    c = me.cell(row=i, column=1, value=texte)
    c.font = Font(name=POLICE, size=12 if i == 1 else 10, bold=gras)
    c.alignment = Alignment(wrap_text=True, vertical="top")
me.column_dimensions["A"].width = 130

wb.move_sheet("Mode d'emploi", offset=-2)
wb.active = 1
wb.save(SORTIE)
print(f"{SORTIE.name} : {derniere - premiere + 1} lignes")
