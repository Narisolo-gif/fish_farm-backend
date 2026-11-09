# Fish Farm Backend

Backend de gestion d'une pisciculture (carpe, tilapia) : reproduction → ponte → éclosion → lot → traitement (hapas) → mise en stock → sortie de stock → grossissement.

## Stack
- Django 5.2, Django REST Framework, SimpleJWT (rotation + blacklist), django-cors-headers (front sur `http://localhost:3000`).
- PostgreSQL 16 via Docker Compose (services `backend` et `db`, variables DB dans `.env`).
- `AUTH_USER_MODEL = "core.Utilisateur"` (AbstractUser + FK `Role` + `statut`). Rôles : `Administrateur`, `Gestionnaire`, `Employé` (permissions dans `apps/core/permissions.py`).
- Routes : `config/urls.py`, un préfixe par app sous `/api/v1/<app>/` ; login JWT `/api/auth/login/`, refresh `/api/auth/refresh/`.

## Commandes
```powershell
docker compose up -d --build
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py test apps
```
Tests E2E Postman : `tests/postman/` (collection séquentielle 00→12, base vide requise, voir `tests/postman/README_TEST.md`).

## Apps (`apps/`)
| App | Modèles principaux |
|---|---|
| core | Role, Utilisateur |
| bassins | Bassin (catégorie, statut), Hapa |
| environnement | ParametreEnvironnemental |
| reproduction | Reproduction, Ponte |
| ecloserie | Eclosion (FK Ponte) |
| lots | Lot (OneToOne Eclosion ; `espece`, `stade`, `parcours_production`) |
| traitement | Traitement (OneToOne Lot), TraitementHapa |
| stocks | MiseEnStock (OneToOne Lot, `finalite`), MiseEnStockHapa |
| provende | TypeProvende, Provende, ConsommationProvende |
| effectifs | MouvementEffectif, EvenementMortalitePerte, CauseMortalite, CategoriePerte, BlocageSaisie, SortieStock, SortieStockLigne |
| grossissement | LotGrossissement |
| monitoring | vide (pas encore implémenté) |

## Registre `effectifs` (cœur du métier)
- `MouvementEffectif` est un journal **append-only** : `save()` sur un objet existant et `delete()` lèvent une erreur. Une correction se fait **uniquement** par un mouvement `ANNULATION` lié (`annule_mouvement`), via `annuler_mouvement`.
- Les effectifs ne sont jamais stockés : ils se calculent avec Σ(sens × quantité) dans `apps/effectifs/calculs.py` (`effectif_vivant`, `effectifs_du_bassin`, `effectifs_par_hapa`, `lots_presents_dans_hapa`).
- Toute opération qui fait bouger des poissons (traitement, mise en stock, mortalité/perte, sortie) écrit ses mouvements via `apps/effectifs/services.py` (`enregistrer_mouvement_operation`), dans la même transaction.
- La mortalité, la perte et les sorties de stock sont **centralisées dans `effectifs`**, et la consommation dans `provende.ConsommationProvende`. Il n'y a pas de modèle de mortalité ou de consommation par domaine : `architecture_backend.md` est obsolète sur ce point.
- `effectifs` ne doit pas importer `grossissement` : il passe par les signaux `sortie_stock_validee` et `sortie_stock_avant_annulation` (`apps/effectifs/signals.py`), consommés par `apps/grossissement/receivers.py`.

## Cycle de vie d'un lot (`Lot.stade`)
`CREEE` → `EN_TRAITEMENT` (seulement si `parcours_production = TRAITEMENT_REQUIS` ; bassin de catégorie TRAITEMENT, hapas obligatoires) → `EN_STOCK` → (SortieStock BROUILLON → VALIDEE) `EN_GROSSISSEMENT` → `CLOTUREE`.

## Conventions
- Chaîne des couches : `models` (contraintes en base : `CheckConstraint`, `UniqueConstraint` conditionnelles) → `serializers` → `viewsets` minces → `services.py` pour la logique métier.
- Services : `@transaction.atomic`, verrous `select_for_update()` sur Lot/Bassin/Hapa, erreurs `rest_framework.exceptions.ValidationError({"id_lot": "..."})` dont les clés suivent les champs API (`id_lot`, `id_bassin`, `id_hapa`).
- Rien n'est supprimé physiquement pour les données métier : on annule avec un `statut` et un `motif_annulation` obligatoire (imposé par une contrainte), et on renseigne `annulee_par` / `annulee_le`.
- Les noms, messages d'erreur et commentaires sont en **français**.

## Points connus à corriger
- `config/settings.py` : `SECRET_KEY` en dur, `DEBUG=True`, `ALLOWED_HOSTS=[]` ; durée de vie du jeton d'accès JWT (15 j) supérieure à celle du refresh (7 j).
- `apps.core.urls` est inclus deux fois (`/api/` et `/api/v1/auth/`).
- Restes : dossier `fish_farm-backend/` (app vide, non installée), `db.sqlite3` et `_test_settings_effectifs.py` vides.
