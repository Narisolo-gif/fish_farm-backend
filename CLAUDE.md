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
| grossissement | LotGrossissement, Pesee (une par lot et par date), IndicateurLot (cache dérivé, OneToOne Pesee) |
| monitoring | vide (pas encore implémenté) |

## Registre `effectifs` (cœur du métier)
- `MouvementEffectif` est un journal **append-only** : `save()` sur un objet existant et `delete()` lèvent une erreur. Une correction se fait **uniquement** par un mouvement `ANNULATION` lié (`annule_mouvement`), via `annuler_mouvement`.
- Les effectifs ne sont jamais stockés : ils se calculent avec Σ(sens × quantité) dans `apps/effectifs/calculs.py` (`effectif_vivant`, `effectifs_du_bassin`, `effectifs_par_hapa`, `lots_presents_dans_hapa`).
- Toute opération qui fait bouger des poissons (traitement, mise en stock, mortalité/perte, sortie) écrit ses mouvements via `apps/effectifs/services.py` (`enregistrer_mouvement_operation`), dans la même transaction.
- La mortalité, la perte et les sorties de stock sont **centralisées dans `effectifs`**, et la consommation dans `provende.ConsommationProvende`. Il n'y a pas de modèle de mortalité ou de consommation par domaine.
- `effectifs` ne doit pas importer `grossissement` : il passe par les signaux `sortie_stock_avant_annulation`, `mouvement_avant_annulation` et `mouvement_avant_enregistrement` (`apps/effectifs/signals.py`), consommés par `apps/grossissement/receivers.py`.
- La sortie vers le grossissement est faite par `grossissement.services.valider_sortie_grossissement` (une transaction, sans brouillon ni signal ; `SortieStock.statut` vaut `VALIDEE` ou `ANNULEE`), qui s'appuie sur `effectifs.services.preparer_sortie_stock` et `enregistrer_sortie_stock_validee`. Aperçu : `apercu_sortie_grossissement`.

## Pesée de suivi (`grossissement`)
- Une `Pesee` porte sur un lot de grossissement et une date : la date est libre (≤ aujourd'hui, ≥ mise en place) mais strictement postérieure à la dernière pesée **valide** du lot (`UniqueConstraint` lot + date sur `VALIDEE`).
- Seule la dernière pesée valide d'un lot actif est corrigeable (`modifier_pesee`, date figée) ou annulable (`annuler_pesee`, motif obligatoire) ; jamais de suppression. Une pesée, même annulée, empêche d'annuler la sortie de stock (`lot_a_historique`).
- Échantillon : pourcentage cible → taille cible (confirmation `confirmer_echantillon_non_conforme` si en dessous, comme P0) et `pourcentage_reel` calculé.
- `recalculer_indicateurs_bassin` recalcule tout le bassin à chaque pesée, correction ou annulation, sur les pesées valides, en **relisant l'effectif dans le registre** ; l'`effectif_vivant` stocké sur la pesée n'est qu'une trace de la saisie.
- Calendrier indicatif (`calculs.etat_calendrier`) : J30, J60 puis tous les 15 jours, sans limite. Prochaine pesée = première échéance strictement postérieure à la dernière pesée valide (J30 sinon) ; retard si la date de référence dépasse la date prévue plus `TOLERANCE_RETARD_PESEE_JOURS`. Jamais bloquant.
- Constantes métier en tête de `calculs.py` : calendrier, `TOLERANCE_RETARD_PESEE_JOURS = 0`, `SEUIL_POIDS_VENTE_G = {"TILAPIA": 250, "CARPE": 300}` (jugé sur la **dernière** pesée valide : signal « fin de cycle possible » et précondition de clôture ; jamais de clôture automatique).

## Suivi de cycle (`GET lots-grossissement/{id}/suivi/?date_reference=`)
- `apps/grossissement/suivi.py` (`suivi_lot`) assemble le contrat en **lecture seule** : il lit `IndicateurLot` sans rien recalculer. Tout est vu à `date_reference` (défaut : aujourd'hui) ; pour un lot clôturé, jour de cycle et calendrier s'arrêtent à `date_cloture`, sans prochaine pesée ni alerte.
- Format (`SuiviLotSerializer`) : décimaux en nombres JSON, valeurs non calculables à `null`, toutes les clés toujours présentes, noms et valeurs du code (`ACTIF`, `code`, `CARPE`…). Alertes `PESEE_EN_RETARD` (attention) et `POIDS_VENTE_ATTEINT` (info), calculées à la volée.
- Exemples pour le frontend : `docs/contrats/*.exemple.json`, générés par `GENERER_EXEMPLES=1 python manage.py test apps.grossissement.tests.test_suivi` et contrôlés par ce test.

## Clôture d'un lot de grossissement (voir `context/CONTEXTE_CLOTURE_LOT.md`)
- `preparer_cloture` (aperçu `GET …/{id}/apercu-cloture/`) puis `cloturer_lot_grossissement` (`POST …/cloturer/`, corps `{observation?}`) : lot `ACTIF` et dernière pesée ≥ seuil de l'espèce ; date = aujourd'hui, poids moyen final = dernière pesée, effectif final = registre. Une transaction, **aucun mouvement** ; lot d'origine `CLOTUREE`.
- Évacuation séparée et différable : `evacuer_lot_grossissement` (`POST …/evacuer/`), `SORTIE` de l'effectif final datée de la clôture, `evacue_le` / `evacue_par`. Propriété `evacuation_requise`.
- Registre figé : signal `effectifs.signals.mouvement_avant_enregistrement` (émis par `enregistrer_mouvement`) consommé par `grossissement.receivers.refuser_mouvement_lot_cloture` ; nouveau lot de même espèce refusé tant que le bassin n'est pas évacué.
- Historique : `GET lots-grossissement/historique-clotures/` (écart de poids %, taux de survie, évacuation).

## Cycle de vie d'un lot (`Lot.stade`)
`CREEE` → `EN_TRAITEMENT` (seulement si `parcours_production = TRAITEMENT_REQUIS` ; bassin de catégorie TRAITEMENT, hapas obligatoires) → `EN_STOCK` → (sortie totale validée et création du lot, une transaction) `EN_GROSSISSEMENT` → `CLOTUREE`.

## Conventions
- Fuseau : `TIME_ZONE = "Indian/Antananarivo"` ; les dates-heures sont stockées en UTC, les dates locales (aujourd'hui, jour d'une distribution) suivent ce fuseau.
- Chaîne des couches : `models` (contraintes en base : `CheckConstraint`, `UniqueConstraint` conditionnelles) → `serializers` → `viewsets` minces → `services.py` pour la logique métier.
- Services : `@transaction.atomic`, verrous `select_for_update()` sur Lot/Bassin/Hapa, erreurs `rest_framework.exceptions.ValidationError({"id_lot": "..."})` dont les clés suivent les champs API (`id_lot`, `id_bassin`, `id_hapa`).
- Rien n'est supprimé physiquement pour les données métier : on annule avec un `statut` et un `motif_annulation` obligatoire (imposé par une contrainte), et on renseigne `annulee_par` / `annulee_le`.
- Les noms, messages d'erreur et commentaires sont en **français**.

## Points connus à corriger
- `config/settings.py` : `SECRET_KEY` en dur, `DEBUG=True`, `ALLOWED_HOSTS=[]` ; durée de vie du jeton d'accès JWT (15 j) supérieure à celle du refresh (7 j).
- `apps.core.urls` est inclus deux fois (`/api/` et `/api/v1/auth/`).
