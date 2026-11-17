# Collection Postman « flux complet » : plan d'implémentation

> **Pour les agents :** sous-compétence requise : superpowers:subagent-driven-development (recommandée) ou superpowers:executing-plans, tâche par tâche. Les étapes utilisent des cases à cocher (`- [ ]`).

**Objectif :** générer une collection Postman qui joue le parcours complet de l'exploitation, avec le rôle réel de chaque action et les refus de la matrice, et qui remplit une base de démonstration.

**Architecture :** le script `tests/postman/generer_flux_complet.py` décrit le scénario en données (`Etape`) et produit le JSON Postman. Le même scénario est **rejoué par un test Django** (`apps/core/tests/test_collection_flux_complet.py`), qui exécute chaque étape avec un vrai jeton JWT sur la base de test : les erreurs de corps, de dates ou de droits sont détectées sans newman.

**Stack :** Python 3 (bibliothèque standard seulement, pour le générateur), Django/DRF (rejoueur), Postman/newman, `node --check` pour la syntaxe JS.

**Spécification :** `context/CONCEPTION_POSTMAN_FLUX_COMPLET.md`.

## Contraintes globales

- Variables Postman préfixées `fc_` ; références uniques via `{{fc_run}}`.
- Dates fixes en 2026. Seules la clôture du traitement de T2 et la clôture du lot P sont datées du jour. Tous les lots mis en grossissement (P, G1, G2) passent par un traitement clôturé à une date passée (tâche 0).
- Rôles : directeur (référentiels, création de la chaîne, décisions, corrections), employé (mortalités, pertes, provende, environnement, pesées), gestionnaire (lectures). Le compte administrateur ne sert qu'à la connexion de support, à aucune étape nominale.
- Les refus attendent 403 et n'écrivent rien ; un accès sans jeton attend 401.
- Les G1 et G2 restent en retard de pesée (alerte voulue). Pas de pesée récente pour eux.
- Les valeurs exactes des indicateurs ne sont pas vérifiées.
- Pas de commit : le développeur commite.

## Points de vigilance (à couvrir par les tests)

1. **Deuxième exécution sur la même base** : tout passe encore (références uniques). Test : tâche 2, le rejoueur joue le scénario deux fois de suite.
2. **Un refus qui écrirait quelque chose** : aucun compteur ne bouge. Test : tâche 1, le rejoueur compare les compteurs des modèles métier avant et après chaque refus.
3. **Une étape nominale jouée avec un rôle inadapté** (administrateur, ou gestionnaire en écriture). Test : tâche 1, test de structure.
4. **Une date de cycle incohérente** (pesée antérieure à la précédente, distribution hors d'un lot en cours) : la requête échoue au rejeu. Test : tâche 2, chaque étape vérifie son code HTTP.
5. **Une variable capturée vide** (identifiant non trouvé) : le rejoueur échoue en nommant l'étape. Test : tâche 1.

---

### Tâche 0 : date de clôture d'un traitement (évolution de l'API)

**Fichiers :**
- Modifier : `apps/traitement/services.py` (`cloturer_traitement`, `preparer_cloture_traitement`, `_effectifs_finaux`), `apps/traitement/viewsets.py` (`cloturer`, `apercu_cloture`), `apps/stocks/services.py` (`creer_mise_en_stock`)
- Test : `apps/traitement/tests/test_cloture.py`, `apps/stocks/tests/` (nouveau test dans le fichier existant de la mise en stock)

**Interfaces (produites) :**
- `cloturer_traitement(traitement_id, date_cloture=None)` : défaut `timezone.localdate()`. Les effectifs finaux sont lus dans le registre à `date_cloture`. Refus en `ValidationError({"date_cloture": …})` si la date est future, antérieure à `date_ouverture`, ou si un mouvement d'un hapa du traitement (même espèce) est daté après elle.
- `preparer_cloture_traitement(traitement, date_cloture=None)` : même date et mêmes contrôles ; un refus de date se lit dans `raison_refus`.
- API :
  - `POST …/traitement/{id}/cloturer/` accepte `{"date_cloture": "AAAA-MM-JJ"}` (facultatif ; un format invalide donne 400) ;
  - `GET …/traitement/{id}/apercu-cloture/?date_cloture=AAAA-MM-JJ` (facultatif).
- Mise en stock : `date_mise_en_stock` (date locale) antérieure à `traitement.date_cloture` → `ValidationError({"date_mise_en_stock": "La mise en stock ne peut pas précéder la clôture du traitement (AAAA-MM-JJ)."})`.

- [ ] **Étape 1 : écrire les tests qui échouent.**
  - `test_cloture_a_une_date_passee` : un traitement ouvert il y a 20 jours, avec une mortalité il y a 15 jours, clôturé à J-10 → `date_cloture` J-10 et `effectif_final` = initial − mortalité ;
  - `test_date_de_cloture_refusee` : future → 400 ; avant l'ouverture → 400 ; avant une mortalité du traitement → 400 ;
  - `test_apercu_a_la_date_demandee` ;
  - `test_mise_en_stock_avant_la_cloture_refusee`.
- [ ] **Étape 2 : vérifier l'échec.**
- [ ] **Étape 3 : implémenter.**
- [ ] **Étape 4 :** `docker compose exec backend python manage.py test apps.traitement apps.stocks` → OK. Mettre à jour `CLAUDE.md` (section « Clôture du traitement ») et `context/REGLES_DE_GESTION_FISH_FARM.md` (sections 4 et 5).
- [ ] **Étape 5 : pas de commit.**

### Tâche 1 : socle du générateur et du rejoueur

**Fichiers :**
- Créer : `tests/postman/generer_flux_complet.py`, `tests/postman/test_generer_flux_complet.py` (tests sans base), `apps/core/tests/test_collection_flux_complet.py` (rejoueur)

**Interfaces (produites) :**
- `@dataclass Etape(nom: str, role: str | None, methode: str, chemin: str, corps: dict | None = None, attendu: int = 200, capture: dict[str, str] = {}, verifs: list[str] = [], refus: bool = False)`.
  - `role` vaut `"admin" | "directeur" | "gestionnaire" | "employe"`, ou `None` pour un appel sans jeton.
  - `chemin` commence par `/api/` et peut contenir des `{{fc_…}}`.
  - `capture` associe une variable à un champ de la réponse, en chemin pointé (`"id"`, `"0.id"`, `"access"`).
  - `verifs` contient des lignes JS `pm.test(...)` supplémentaires.
- `connexions() -> list[Etape]` : quatre connexions `POST /api/auth/login/` (identifiants de l'environnement : `admin_username`, `director_username`, `manager_username`, `employee_username`), qui capturent `fc_token_<role>` depuis `access`.
- `construire_dossiers() -> list[tuple[str, list[Etape]]]` : le scénario (ici, les connexions et un dossier minimal qui crée un bassin en directeur et vérifie le refus de la gérante).
- `generer_collection(dossiers) -> dict` : collection Postman v2.1. Un `prerequest` au niveau de la collection fixe `fc_run` s'il est vide. Chaque requête porte `auth: bearer {{fc_token_<role>}}` (ou `noauth`) ; son script de test vérifie `attendu`, enregistre les captures et ajoute `verifs`.
- `main()` : écrit `tests/postman/flux_complet.postman_collection.json` (indentation 2, UTF-8).
- Rejoueur : `rejouer(test, dossiers, variables) -> dict`. Il remplace les `{{…}}` (variables d'environnement : identifiants de démonstration ; `fc_run` tiré par le test), envoie la requête avec `APIClient` et l'en-tête `Authorization: Bearer`, compare le code HTTP et enregistre les captures. Une capture vide fait échouer le test en nommant l'étape. Pour une étape `refus=True`, il vérifie que les compteurs de `Lot`, `MouvementEffectif`, `EvenementMortalitePerte`, `ConsommationProvende`, `Pesee`, `ParametreEnvironnemental`, `Traitement`, `MiseEnStock`, `LotGrossissement` et `Utilisateur` n'ont pas bougé.

- [ ] **Étape 1 : écrire les tests qui échouent.**
  - Dans `tests/postman/test_generer_flux_complet.py` (`unittest`, import par chemin) :
    - `test_chaque_requete_porte_le_jeton_de_son_role` ;
    - `test_aucune_etape_nominale_en_administrateur` : seules les connexions utilisent le rôle `admin` ;
    - `test_refus_attendent_403_ou_401` ;
    - `test_variables_prefixees_fc` : toutes les captures commencent par `fc_` ;
    - `test_collection_v21_valide` : `info.schema` est v2.1 et chaque requête a des `event` de test.
  - Dans `apps/core/tests/test_collection_flux_complet.py` : `test_scenario_rejoue_avec_les_vrais_droits`, qui crée les quatre comptes de démonstration avec les mots de passe de l'environnement puis rejoue `construire_dossiers()`.
- [ ] **Étape 2 : lancer les tests et vérifier l'échec.**
  - `py -m unittest tests.postman.test_generer_flux_complet` → ImportError (module absent) ;
  - `docker compose exec backend python manage.py test apps.core.tests.test_collection_flux_complet` → ImportError.
- [ ] **Étape 3 : implémenter `Etape`, `connexions`, `generer_collection`, `main`, le dossier minimal et `rejouer`.**
- [ ] **Étape 4 : lancer les deux suites et vérifier qu'elles passent.** Lancer aussi `py tests/postman/generer_flux_complet.py`, puis contrôler la syntaxe JS des scripts générés avec `node --check` : aucune erreur.
- [ ] **Étape 5 : pas de commit.**

### Tâche 2 : le scénario complet

**Fichiers :**
- Modifier : `tests/postman/generer_flux_complet.py` (`construire_dossiers`)
- Modifier : `apps/core/tests/test_collection_flux_complet.py` (rejeu deux fois)

**Interfaces :** consomme `Etape`, `connexions`, `rejouer` (tâche 1).

Dossiers (ordre et contenu fixés par la section 5 de la conception) :
- `00 Connexions` ; `01 Comptes` (refus : gérante qui s'attribue le rôle Administrateur ; sans jeton → 401) ;
- `02 Référentiels` (directeur : bassins de reproduction, de traitement, de stock et de grossissement, hapas, provendes ; refus : gérante crée un bassin) ;
- `03 Lot P : naissance, traitement et stock` (directeur : reproduction → ponte → éclosion → lot `TRAITEMENT_REQUIS` → ouverture du traitement début janvier 2026 ; employé : mortalités sur les hapas ; directeur : aperçu puis clôture datée de mi-janvier, mise en stock à la même date ou après ; refus : employé crée un lot, gérante met en stock) ;
- `04 Lot P : sortie et cycle` :
  - le directeur fait l'aperçu puis la sortie (P0), en février 2026 ;
  - l'employé saisit mortalités, provende (hebdomadaire), mesures environnementales et pesées : J30, J60, puis tous les 15 jours jusqu'au seuil de 250 g, et une pesée finale en novembre 2026 ;
  - le directeur annule une mortalité saisie par erreur, qui est ressaisie ensuite ;
  - refus : gérante valide la sortie, employé annule une mortalité, employé corrige une pesée, gérante saisit une pesée ;
- `05 Lot P : clôture` :
  - le directeur fait l'aperçu, la clôture puis l'évacuation ;
  - la gérante lit l'historique et le suivi ;
  - refus : employé clôture, gérante évacue ;
- `06 Lots T1 et T2 : traitement` :
  - le directeur crée deux lots `TRAITEMENT_REQUIS` et ouvre leurs traitements ;
  - l'employé saisit des mortalités sur leurs hapas ;
  - pour T2 : aperçu de clôture (lu par l'employé), clôture par le directeur, puis mise en stock du jour ;
  - refus : employé ouvre et clôture un traitement ;
- `07 Lots G1 et G2 : grossissement en cours` :
  - le directeur crée deux lots (CARPE et TILAPIA) `TRAITEMENT_REQUIS`, ouvre leurs traitements en juin 2026, les clôture en juillet (l'employé a saisi des mortalités entre-temps), les met en stock, puis les sort vers le même bassin de grossissement avec une entrée le 1er août 2026 ;
  - l'employé saisit provende, mortalités par espèce, mesures environnementales et pesées J30, J60 et J75 ;
  - la gérante lit le suivi de G1, qui contient l'alerte `PESEE_EN_RETARD` et dont la prochaine pesée est J90 au 2026-10-30.

- [ ] **Étape 1 : écrire le test qui échoue.** `test_scenario_rejoue_deux_fois` rejoue `construire_dossiers()` deux fois avec deux `fc_run` différents. Après le premier rejeu, il vérifie :
  - le lot P : stade `CLOTUREE` et lot de grossissement `evacue_le` renseigné ;
  - T1 : traitement `EN_COURS` ;
  - T2 : lot `EN_STOCK` ;
  - G1 et G2 : `ACTIF`, et `pesee_en_retard` vrai dans la fiche.
- [ ] **Étape 2 : lancer le test et vérifier l'échec** (les dossiers sont absents).
- [ ] **Étape 3 : écrire les dossiers dans `construire_dossiers()`.** Les `verifs` contrôlent les identifiants, le stade ou le statut, le poids moyen initial (sortie), les clés du contrat de suivi, l'alerte de retard et la présence du lot P dans l'historique.
- [ ] **Étape 4 : lancer les tests de la tâche 1 et de la tâche 2, régénérer la collection et contrôler la syntaxe JS** ; tout doit passer.
- [ ] **Étape 5 : pas de commit.**

### Tâche 3 : archivage et documentation

**Fichiers :**
- Déplacer (`git mv`) : `tests/postman/grossissement_sortie_lot.postman_collection.json` → `tests/postman/archives/grossissement_sortie_lot.postman_collection.json`
- Modifier : `tests/postman/README_TEST.md` (nouvelle section en tête, renvoi vers l'archive pour les anciens dossiers), `CLAUDE.md` (ligne Postman), `context/README.md`, `context/CONCEPTION_POSTMAN_FLUX_COMPLET.md` (statut : implémentée)

- [ ] **Étape 1 :** déplacer l'ancienne collection et vérifier qu'aucun test ni document actif n'y renvoie comme collection en vigueur (`grep`).
- [ ] **Étape 2 :** README avec les éléments suivants :
  - prérequis (quatre comptes de démonstration, `migrate`) ;
  - commande newman : `npx newman@6.1.3 run tests/postman/flux_complet.postman_collection.json -e tests/postman/pisciculture_local.postman_environment.json --env-var "base_url=http://localhost:8000"` ;
  - régénération : `py tests/postman/generer_flux_complet.py` ;
  - rejeu : `docker compose exec backend python manage.py test apps.core.tests.test_collection_flux_complet` ;
  - ce que la base contient après exécution ;
  - pesées récentes de G1 et G2 à faire pendant les tests après l'intégration du frontend.
- [ ] **Étape 3 :** suite complète `docker compose exec backend python manage.py test apps` → OK.
- [ ] **Étape 4 : pas de commit.**
