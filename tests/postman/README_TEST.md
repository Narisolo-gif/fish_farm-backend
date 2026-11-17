# Tests fonctionnels E2E Postman

## Base propre et comptes de test

Les scripts `seed_mock_provendes` ne sont pas nécessaires : les migrations insèrent les types de provende, la cause `Inconnue` et les catégories de perte. Aucun seed de rôles/utilisateurs n'a été trouvé.

Depuis la racine du dépôt, avec Docker Desktop et un `.env` local configuré pour PostgreSQL :

```powershell
docker compose down -v
docker compose up -d --build
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py shell -c "from apps.core.models import Role, Utilisateur; roles={n:Role.objects.get_or_create(libelle=n)[0] for n in ('Administrateur','Gestionnaire','Employé')}; comptes=(('admin_demo','Administrateur'),('gestionnaire_demo','Gestionnaire'),('employe_demo','Employé')); [Utilisateur.objects.get_or_create(username=u, defaults={'role':roles[r], 'statut':'ACTIF'}) for u,r in comptes]; [(lambda x: (x.set_password('ChangeMe-Local-2026!'), setattr(x,'is_active',True), x.save()))(Utilisateur.objects.get(username=u)) for u,_ in comptes]"
```

Les identifiants du fichier d'environnement sont **des exemples locaux**. Remplacez-les si vous changez la commande de création. Le service `backend` écoute le port `8000`.

## Exécution

- **Postman Desktop, étape par étape :**
  1. Démarrez Docker Desktop, puis exécutez les commandes de la section précédente depuis la racine du dépôt. Attendez que `backend` et `db` soient démarrés et que la migration ainsi que la création des comptes se terminent.
  2. Dans Postman, cliquez sur **Import** et importez ces deux fichiers :
     - `tests/postman/pisciculture_e2e.postman_collection.json`
     - `tests/postman/pisciculture_local.postman_environment.json`
  3. Dans le sélecteur d'environnement en haut à droite, choisissez **Pisciculture local - exemple**. Vérifiez que `base_url` vaut `http://localhost:8000` et que les trois identifiants (`admin_username`, `manager_username`, `employee_username`) correspondent aux comptes créés par la commande ci-dessus.
  4. Dans la barre latérale **Collections**, ouvrez **Pisciculture - tests fonctionnels E2E**, puis cliquez sur **Run** (ou sur `...` > **Run collection** selon la version de Postman).
  5. Dans le Runner, vérifiez que tous les dossiers sont sélectionnés et conservez leur ordre numérique, de **00 Préparation** à **12 Contrôles finaux**. Ne sélectionnez pas seulement les requêtes individuelles : les dossiers suivants réutilisent des identifiants et des jetons créés par les précédents.
  6. Choisissez l'environnement **Pisciculture local - exemple**, laissez **Iterations** à `1`, puis cliquez sur **Run Pisciculture - tests fonctionnels E2E**.
  7. À la fin, consultez le résumé du Runner et la console Postman pour les assertions et les soldes finaux décrits ci-dessous. Un échec dans un dossier peut entraîner des échecs en cascade dans les dossiers suivants.

  La collection est séquentielle et modifie les données. Elle doit être exécutée sur une base de test vide et ne doit pas être relancée sur les données laissées par un parcours précédent. Pour repartir de zéro, réexécutez la préparation avec `docker compose down -v`, `docker compose up -d --build`, les migrations et la création des comptes ci-dessus.
- Depuis Newman dans un conteneur Docker (Windows/macOS) :

```powershell
$postmanDir = (Resolve-Path .\tests\postman).Path
docker run --rm -v "${postmanDir}:/etc/newman" -w /etc/newman postman/newman:alpine run pisciculture_e2e.postman_collection.json -e pisciculture_local.postman_environment.json --global-var "base_url=http://host.docker.internal:8000"
```

- Depuis Newman sur Linux : utilisez `--network host --global-var "base_url=http://127.0.0.1:8000"` à la place du montage/URL Windows ci-dessus.
- Un dossier isolé exige ses variables/identifiants issus des dossiers prérequis. Exécutez d'abord ces prérequis dans l'ordre; les pré-requêtes nomment le dossier requis si une variable manque.
- Pour isoler ensuite un dossier dans Newman, exportez l'environnement après le parcours complet avec `--export-environment /etc/newman/reprise.postman_environment.json`, puis réutilisez ce fichier avec `-e`; il contient des jetons et identifiants de test, ne le commitez pas.
- Ne lancez pas la collection entre **23:50 et 00:10 heure locale**, afin que les horodatages du jour restent cohérents.

## État final attendu

| Cible | Effectif final |
|---|---:|
| TRT_A / TRT_B | 0 / 0 |
| STK_1 / STK_2 | 0 / 0 |
| STK_3 / STK_4 | 2 000 / 0 |
| GRO | 10 730 |

Les soldes sont lus dans le registre et récapitulés dans la console Postman/Newman. Les sorties de stock de lot C sont annulées puis rejouées; l'ancien lot de grossissement est supprimé.

## Écarts connus entre le scénario demandé et l'API

- `Lot.eclosion` est un `OneToOneField` : le parcours crée une ponte/éclosion distincte par lot au lieu de rattacher les trois lots à une seule éclosion.
- Pour vérifier la neutralité d'un hapa après vidage, le parcours annule l'entrée registre du lot de test VENTE sur STK_4, puis y entre le lot C GROSSISSEMENT.
- La sortie du lot B de finalité `VENTE` est refusée (aperçu et validation, 400 sur `id_lot`). Il n'existe plus de brouillon de sortie.
- La liste des mouvements ne filtre pas `type_mouvement` (le paramètre est ignoré). Le test demandé d'un filtre `ENTREE` est conservé et peut échouer.
- Un événement de mortalité ou une distribution sans hapa est accepté en bassin ayant du poisson, malgré les refus attendus dans le scénario. Les assertions sont conservées; si l'API accepte, le test annule ensuite l'enregistrement inattendu.
- Aucun endpoint santé dédié n'existe dans les URLs consultées; la préparation vérifie la disponibilité par une lecture authentifiée réelle.
- Le dossier final attend une seule ligne active de grossissement après clôture de C; l'API `?statut=ACTIF` permet de la vérifier.
- La clôture d'un lot de grossissement exige `poids_final_g` et enregistre un mouvement `SORTIE` de l'effectif vivant : le solde final GRO attendu est donc 0.
- La règle « au plus deux lots d'espèces différentes par bassin » n'a pas de requête dédiée : le scénario n'a pas de second lot CARPE sortable vers GRO pendant que C est actif. Elle est couverte par les tests Django de `apps.grossissement`.
- Les scripts de test sont entourés d'un bloc `{ … }` : sans cela, newman 6 refuse `let data` (conflit avec la variable globale héritée `data` du sandbox).
- Avec `ALLOWED_HOSTS=[]`, Django refuse l'hôte `host.docker.internal` : lancer newman depuis la machine hôte (`npx newman@6.1.3 run … --env-var "base_url=http://localhost:8000"`).
- Constaté le 2026-11-14 sur base vide : des échecs antérieurs (01.12, 05.03, 06.11, 09.x…) empêchent la création de `lot_gross_c`, et toutes les requêtes du lot C du dossier 10 échouent en cascade.

Les modules environnement, pesée/répartition et toute route non présente dans les URLs réelles sont ignorés dans la collection E2E.

## Pesées (collection grossissement)

La pesée sur échantillon est testée dans le dossier **06 Pesées** de `grossissement_sortie_lot.postman_collection.json`. Ce dossier est autonome : il crée ses bassins, ses lots et leur effectif, avec des références uniques par exécution. On peut donc le lancer seul ou après les dossiers 00 à 05 :

```powershell
npx newman@6.1.3 run tests/postman/grossissement_sortie_lot.postman_collection.json -e tests/postman/pisciculture_local.postman_environment.json --env-var "base_url=http://localhost:8000" --folder "06 Pesées"
```

Valeur à vérifier à la main : le lot A compte 1 000 poissons ; à 10 %, la cible est de 100 poissons ; 100 poissons pour 2 537,50 g donnent 25,375 g, soit **25,38 g** après arrondi.

Une pesée porte sur **un lot** de grossissement et une date (migrations `grossissement` 0007 et 0008 : les anciennes lignes `PeseeLot` deviennent des `Pesee`, identifiants conservés). L'utilisateur saisit seulement la date, le nombre de poissons pesés et le poids total. Le lot, l'espèce, le bassin, l'effectif vivant (lu dans le registre), la taille cible, le poids moyen, le pourcentage réel (`pourcentage_reel` = pesés ÷ vivants) et l'utilisateur sont déduits ou calculés :

```json
{
  "id_lot_grossissement": 7,
  "date": "2026-04-01",
  "nombre_poissons_peses": 100,
  "poids_total_g": "2537.50"
}
```

- `POST /api/v1/grossissement/pesees/apercu/` : formulaire prérempli (effectif vivant, cible, date de la dernière pesée, date minimale ; poids moyen et pourcentage réel si les mesures sont fournies), rien n'est écrit ;
- la date est libre dans le cycle (aucune échéance imposée), au plus aujourd'hui, au plus tôt la mise en place, et strictement après la dernière pesée valide du lot (date unique par lot) ;
- `PATCH …/{id}/` corrige les mesures de la **dernière** pesée valide seulement (date et lot figés) ; `POST …/{id}/annuler/` (motif obligatoire) l'annule, et la précédente redevient corrigeable. Aucune suppression : `DELETE` répond 405 ;
- la fiche du lot expose `poids_moyen_actuel`, `date_derniere_pesee`, `derniers_indicateurs` et, à titre indicatif, `etape_courante`, `prochaine_echeance_pesee` et `pesee_en_retard` (calendrier J30, J60 puis tous les 15 jours, sans limite, jamais bloquant).

Dans le dossier 06, les anciens cas propres à la pesée de bassin (lot d'un autre bassin, lot en double, bassin STOCKS ou inactif, pesée partiellement invalide) sont remplacés par : bassin saisi refusé (déduit du lot), ancien format `lignes` refusé, lot inexistant, aperçu sans puis avec mesures, champ calculé refusé, annulation (refusée si ce n'est pas la dernière ou sans motif) et `DELETE` en 405. Les pesées de A et T du même jour sont deux requêtes (06.37 et 06.37b).

## Sortie vers le grossissement en une seule transaction

Il n'y a plus de brouillon ni de validation en deux temps. Deux appels, réservés à l'Administrateur et au Gestionnaire :

- `POST /api/v1/grossissement/lots-grossissement/apercu-sortie/` : aperçu en lecture seule (quantité sortie, lignes par hapa, code proposé, taille cible, poids moyen initial calculé, conformité de l'échantillon, écart). Mêmes contrôles que la validation ; rien n'est écrit.
- `POST /api/v1/grossissement/lots-grossissement/` : sortie totale du stock, mouvements `SORTIE` et `ENTREE`, lot d'origine `EN_GROSSISSEMENT` et création du lot, dans une seule transaction.

```json
{
  "id_lot": 12,
  "id_bassin_destination": 4,
  "date_pesee_initiale": "2026-03-01",
  "pourcentage_echantillon": "10",
  "nombre_poissons_peses": 200,
  "poids_total_g": "4000.00",
  "confirmer_echantillon_non_conforme": false,
  "code": "",
  "date_mise_en_place": "2026-03-02",
  "effectif_entrant": 2000,
  "justification_ecart": ""
}
```

`code` vide : le code proposé (`GR-AAAA-NNNN`) est retenu. Le champ `poids_initial_g` est renommé `poids_moyen_initial` (migration `0006`). Ni l'un ni l'autre n'est accepté en écriture, à la validation comme en `PATCH` du lot : le poids moyen initial est calculé depuis la pesée initiale (P0) et tracé, en lecture seule, avec `date_pesee_initiale`, `pourcentage_echantillon_initial`, `nombre_poissons_peses_initial` et `poids_total_initial_g`. Les anciennes routes `POST /api/v1/effectifs/sorties-stock/` et `PATCH …/{id}/` répondent 405, `…/{id}/valider/` répond 404 ; la lecture et `…/{id}/annuler/` restent disponibles.

Les requêtes adaptées des dossiers 03 à 07 (collection grossissement) et 08 à 10 (collection E2E) envoient un échantillon P0 à la taille cible (10 %) pesant 20 g par poisson (40 g pour le lot B du dossier 07), soit un `poids_moyen_initial` de 20,00 g, comme l'ancien `poids_initial_g`.

### Dossier « 08 Sortie grossissement »

Dossier indépendant de `grossissement_sortie_lot.postman_collection.json` : il se connecte avec les trois comptes de l'environnement et crée ses bassins, ses cinq lots (2 000 poissons chacun) et leurs stocks, à dates fixes (stock le 2026-02-10, pesée initiale le 2026-03-01, mise en bassin le 2026-03-02) et à références uniques par exécution.

```powershell
npx newman@6.1.3 run tests/postman/grossissement_sortie_lot.postman_collection.json -e tests/postman/pisciculture_local.postman_environment.json --env-var "base_url=http://localhost:8000" --folder "08 Sortie grossissement"
```

Scénarios couverts : aperçu sans écriture ; cas nominal (200 poissons pesés pour 4 000 g, soit 20,00 g, aucune `Pesee`) ; écart en moins justifié ; écart sans justification ; écart en plus refusé ; échantillon non conforme (150 pour une cible de 200) refusé puis accepté avec confirmation ; échantillon supérieur à l'effectif entrant ; dates (mise en bassin avant la pesée, date future) ; bassin d'une autre catégorie, avec hapa, déjà à deux lots, de même espèce ; deux espèces dans un même bassin ; code déjà utilisé puis code saisi ; ancien flux refusé ; permissions (employé, sans authentification, compte désactivé) ; trace P0 en lecture seule. Après chaque refus, le dossier vérifie que rien n'a été écrit : aucune sortie, aucun lot de grossissement, stade `EN_STOCK`, effectif du hapa de stock intact.

Écarts avec le scénario demandé :

- **Écart en plus** : refusé, même justifié (décision du 2026-11-14 : l'effectif entrant ne dépasse jamais la quantité sortie, contrainte `sortie_stock_entrant_lte_sortant` conservée).
- **Date bloquée par le blocage de saisie** : non reproductible par l'API. Le blocage porte sur les hapas d'un traitement clôturé, jamais sur un hapa de stock, et `JOURS_SAISIE_RETROACTIVE` est désactivé (`None`). Les deux contrôles sont appliqués par le service, sans requête dédiée.
- **Autres finalités de sortie** : la sortie de stock n'a que le type `GROSSISSEMENT` ; il n'existe pas d'autre finalité à valider « comme avant ».
- **Bassin avec hapa** : l'API refuse la création d'un hapa en bassin de grossissement. Le dossier crée le bassin en `REPRODUCTION`, y ajoute un hapa, puis le passe en `GROSSISSEMENT`.

## Indicateurs (collection grossissement)

Le dossier **07 Indicateurs** est autonome, à **dates fixes** (cycle de 2026, entrée des lots le 2026-01-01) et à références uniques par exécution : il peut être relancé sur la même base. Il crée ses bassins, ses lots, leurs mortalités et les distributions de provende, puis vérifie l'objet `indicateurs` de chaque pesée :

- un lot seul (S1), dont la biomasse en baisse (IC `null`), une période sans provende, la modification et l'annulation de la dernière pesée (la pesée reste consultable, son instantané disparaît) ;
- deux lots d'espèces différentes dans un même bassin, avec le recalcul croisé de la provende (81,753 kg puis 61,993 kg pour le lot A) ;
- une pesée le jour même de l'entrée (GMQ `null`).

```powershell
npx newman@6.1.3 run tests/postman/grossissement_sortie_lot.postman_collection.json -e tests/postman/pisciculture_local.postman_environment.json --env-var "base_url=http://localhost:8000" --folder "07 Indicateurs"
```

Les instantanés sont recalculés pour tout le bassin à chaque création, correction ou annulation de pesée, à partir des seules pesées valides. L'effectif de chaque pesée est alors relu dans le registre (l'`effectif_vivant` de la pesée reste la trace de la saisie ; celui de `indicateurs` est l'effectif relu). Une distribution ou une mortalité saisie après coup sur une période déjà pesée n'est donc prise en compte qu'à la prochaine opération de pesée du bassin.

## Suivi de cycle (collection grossissement)

Le dossier **09 Suivi de cycle** teste `GET /api/v1/grossissement/lots-grossissement/{id}/suivi/?date_reference=AAAA-MM-JJ`. Il est autonome, à dates fixes et à références uniques par exécution : il recrée le lot S1 du dossier 07 (entrée le 2026-01-01, pesées de 45 g puis 80 g), puis lit le suivi au 2026-01-11 (aucune pesée), au 2026-03-10 (valeurs de l'exemple du contrat) et au 2026-03-25 (pesée J75 en retard de 8 jours). Il vérifie aussi la liste de sélection (`?statut=ACTIF`, `date_entree`, `jour_de_cycle`), la lecture par un employé, le refus sans connexion, une `date_reference` invalide (400) et un lot inexistant (404).

```powershell
npx newman@6.1.3 run tests/postman/grossissement_sortie_lot.postman_collection.json -e tests/postman/pisciculture_local.postman_environment.json --env-var "base_url=http://localhost:8000" --folder "09 Suivi de cycle"
```

Les exemples du contrat pour le frontend sont dans `docs/contrats/` (`suivi_lot.exemple.json`, `suivi_lot_vide.exemple.json`, `lots_en_cours.exemple.json`). Ils sont produits par l'API à partir du même scénario (`GENERER_EXEMPLES=1 python manage.py test apps.grossissement.tests.test_suivi`) ; sans cette variable, le test vérifie qu'ils ont toujours la structure de la réponse.

Fuseau : le serveur est à l'heure d'Antananarivo (`TIME_ZONE = "Indian/Antananarivo"`). Les dates relatives des collections (`dateOnly`) sont calculées à UTC+3 pour coïncider avec la « date du jour » du serveur.

## Clôture (collection grossissement)

Le dossier **10 Clôture** est autonome, à dates fixes et à références uniques : il recrée le lot S1 (CARPE, entrée le 2026-01-01, pesées de 45 g puis 80 g), vérifie que la clôture est refusée sous le seuil (300 g pour la carpe), enregistre une pesée finale de 300 g, puis teste l'aperçu, l'ancien format refusé, le refus pour un employé, la clôture (finaux, message, lot d'origine `CLOTUREE`), la seconde clôture refusée, la mortalité et la distribution de provende refusées après la clôture, l'historique, l'évacuation (puis la seconde refusée), le solde nul et le suivi en lecture seule.

```powershell
npx newman@6.1.3 run tests/postman/grossissement_sortie_lot.postman_collection.json -e tests/postman/pisciculture_local.postman_environment.json --env-var "base_url=http://localhost:8000" --folder "10 Clôture"
```

Clôtures adaptées au nouveau flux (dernière pesée ≥ seuil, date du jour, aucun poids ni motif saisi, évacuation à part) : dossier 05 (pesée finale du lot A, aperçu, clôture, évacuation), dossier 06 (pesée finale du lot B à J-6 avant sa mortalité totale, clôture à effectif nul) et dossier 10 de la collection E2E (pesée finale du lot C à 10 %, clôture, évacuation ; l'ancien format est refusé). La collection E2E fait donc désormais une pesée, la seule.

## Clôture du traitement (collection E2E)

Dossier 05 : l'employé lit le résumé de clôture (`GET /api/v1/traitement/{id}/apercu-cloture/`, 05.00a) mais la clôture lui est refusée (403, 05.00b) ; la clôture par l'administrateur (05.01) renvoie `message` et `mise_en_stock_possible`.
