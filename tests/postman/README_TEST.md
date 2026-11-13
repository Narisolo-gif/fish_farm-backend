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
- La sortie du lot B de finalité `VENTE` est refusée par le service de sortie; aucun brouillon ne peut donc être créé puis annulé. La collection vérifie ce refus.
- La liste des mouvements ne filtre pas `type_mouvement` (le paramètre est ignoré). Le test demandé d'un filtre `ENTREE` est conservé et peut échouer.
- Un événement de mortalité ou une distribution sans hapa est accepté en bassin ayant du poisson, malgré les refus attendus dans le scénario. Les assertions sont conservées; si l'API accepte, le test annule ensuite l'enregistrement inattendu.
- Aucun endpoint santé dédié n'existe dans les URLs consultées; la préparation vérifie la disponibilité par une lecture authentifiée réelle.
- Le dossier final attend une seule ligne active de grossissement après clôture de C; l'API `?statut=ACTIF` permet de la vérifier.
- La clôture d'un lot de grossissement exige `poids_final_g` et enregistre un mouvement `SORTIE` de l'effectif vivant : le solde final GRO attendu est donc 0.
- La règle « au plus deux lots d'espèces différentes par bassin » n'a pas de requête dédiée : le scénario n'a pas de second lot CARPE sortable vers GRO pendant que C est actif. Elle est couverte par les tests Django de `apps.grossissement`.
- Les scripts de test sont entourés d'un bloc `{ … }` : sans cela, newman 6 refuse `let data` (conflit avec la variable globale héritée `data` du sandbox).
- Avec `ALLOWED_HOSTS=[]`, Django refuse l'hôte `host.docker.internal` : lancer newman depuis la machine hôte (`npx newman@6.1.3 run … --env-var "base_url=http://localhost:8000"`).
- Constaté le 2026-11-14 sur base vide : des échecs antérieurs (01.12, 05.03, 06.11, 09.x…) empêchent la création de `lot_gross_c`, et toutes les requêtes du lot C du dossier 10 échouent en cascade.

Les modules environnement, pesée/répartition et toute route non présente dans les URLs réelles sont ignorés.
