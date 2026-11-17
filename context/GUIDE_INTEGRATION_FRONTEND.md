# Fish Farm : guide d'intégration frontend (backend au 2026-11-17)

Pour les développeurs du frontend Next.js : ce que l'API expose, qui a le droit de faire quoi, les formats à gérer et le parcours de chaque écran de production (lot → traitement → stock → grossissement → clôture). Les règles métier détaillées sont dans `REGLES_DE_GESTION_FISH_FARM.md` ; ce guide renvoie aux sections utiles.

Base : `http://localhost:8000`. Toutes les routes métier sont sous `/api/v1/<module>/`.

## 1. Authentification

| Action | Route | Corps / réponse |
|---|---|---|
| Connexion | `POST /api/auth/login/` | `{username, password}` → `{access, refresh}` |
| Rafraîchir | `POST /api/auth/refresh/` | `{refresh}` → `{access, refresh}` (rotation : le refresh précédent est mis en liste noire) |
| Utilisateur courant | `GET /api/auth/me/` | `{id, username, first_name, last_name, email, role, role_id, statut, is_active, …}` |
| Déconnexion | `POST /api/auth/logout/` | voir `apps/core/views.py` |

En-tête : `Authorization: Bearer <access>`. Rôles (`role`) : `Administrateur`, `Gestionnaire`, `Employé`. Un utilisateur dont le `statut` n'est pas `ACTIF` est refusé (403). Non connecté : 401.

Le frontend masque les actions non permises (section 3), mais le backend reste l'autorité : traiter 403 proprement.

## 2. Conventions de l'API

- **Pas de pagination** : les listes sont des tableaux JSON (prévoir défensivement `data.results ?? data`).
- **Décimaux** : les ressources (lots, pesées, mises en stock, distributions, historique…) renvoient des **chaînes** (`"20.00"`) ; les contrats calculés (suivi, aperçus de clôture et de pesée de grossissement) renvoient des **nombres**. Ne pas recalculer : afficher.
- **Dates** : `AAAA-MM-JJ` ; dates-heures ISO avec décalage `+03:00` (fuseau Indian/Antananarivo). Envoyer les dates-heures avec leur décalage.
- **Erreurs 400** : objet `{champ: message}` où le message est une **chaîne ou une liste de chaînes** selon l'origine (normaliser côté frontend). Clés : nom du champ de l'API (`id_lot`, `id_bassin`, `id_hapa`, `date`, `effectif_entrant`, …), `detail` pour une erreur générale, `non_field_errors` possible. Messages en français, affichables tels quels.
- **Champs inconnus** : les formulaires de grossissement (sortie, pesée, clôture) **refusent** tout champ non prévu (400 sur ce champ) ; ailleurs, les champs en lecture seule envoyés sont ignorés. N'envoyer que les champs du formulaire.
- **Utilisateur et dates d'opération** posés par le serveur : ne jamais envoyer `id_user`, `created_at`, etc.
- **Rien n'est supprimé** : les corrections passent par des actions `annuler` (motif obligatoire). Les `DELETE` existants sont des outils de test (voir section 6).
- **Aperçus** : plusieurs actions ont un appel d'aperçu qui n'écrit rien et renvoie les valeurs préremplies et les erreurs : l'utiliser pour les boîtes de validation.

## 3. Matrice des droits (production)

A = Administrateur, G = Gestionnaire, E = Employé.

| Module / action | A | G | E |
|---|:-:|:-:|:-:|
| Lots : lecture | ✓ | ✓ | ✓ |
| Lots : création, modification | ✓ | ✓ | — |
| Traitement : lecture, création, effectifs, aperçu de clôture | ✓ | ✓ | ✓ |
| Traitement : **clôture**, modification, suppression | ✓ | ✓ | — |
| Mise en stock : **création** | ✓ | ✓ | ✓ |
| Mise en stock : lecture, modification | ✓ | ✓ | — (l'Employé ne peut **pas lire** les mises en stock, voir section 6) |
| Registre : solde, par hapa, liste des mouvements | ✓ | ✓ | ✓ |
| Registre : annulation d'un mouvement | ✓ | ✓ | — |
| Mortalité / perte : saisie, lecture, totaux | ✓ | ✓ | ✓ |
| Mortalité / perte : annulation ; causes et catégories (écriture) | ✓ | ✓ | — |
| Provende : distribution (saisie, lecture, totaux, jours manquants) | ✓ | ✓ | ✓ |
| Provende : annulation d'une distribution ; provendes et types (écriture) | ✓ | ✓ | — |
| Sortie vers le grossissement (aperçu, validation), annulation d'une sortie | ✓ | ✓ | — |
| Lots de grossissement : lecture, suivi, origine, aperçu de clôture, historique | ✓ | ✓ | ✓ |
| Lot de grossissement : modification (observation, % de pesée), clôture, évacuation | ✓ | ✓ | — |
| Pesée : saisie, aperçu, lecture | ✓ | ✓ | ✓ |
| Pesée : correction, annulation | ✓ | ✓ | — |
| Paramètres environnementaux (`/api/v1/environnement/`) : lecture, saisie, modification | ✓ | ✓ | ✓ |
| Paramètres environnementaux : suppression d'une mesure | ✓ | — | — |

## 4. Parcours écran par écran

### 4.1 Lot (`/api/v1/lots/`)
- Création : `{reference, id_eclosion, date_creation, parcours_production, observations}` (`TRAITEMENT_REQUIS` ou `SANS_TRAITEMENT`). Espèce et quantité initiale viennent de l'éclosion.
- Stades : `CREEE` → `EN_TRAITEMENT` → `EN_STOCK` → `EN_GROSSISSEMENT` → `CLOTUREE`. Le stade n'est jamais saisi : il change par les opérations ci-dessous.
- Il n'y a **pas** de clôture directe d'un lot : `PATCH /api/v1/lots/{id}/cloturer/` est bloquée (404). La fin de vie d'un lot passe par la clôture du lot de grossissement (4.7).

### 4.2 Traitement (`/api/v1/traitement/`) — règles : section 4
1. **Ouvrir** : `POST /` `{id_lot, id_bassin, id_used_provende, date_ouverture, effectif_initial, observation, hapas: [{id_hapa, effectif_initial}]}` ; somme des hapas = `effectif_initial` ; lot `CREEE`, bassin TRAITEMENT.
2. **Suivre** : `GET /{id}/` (avec les effectifs par hapa) ou `GET /{id}/effectifs/` ; mortalités par hapa (4.5) ; provende par hapa (4.6).
3. **Clôturer** (A, G) :
   - bouton « Clôturer » → `GET /{id}/apercu-cloture/` → boîte de validation en lecture seule : `reference_lot`, `espece`, `reference_bassin`, `date_ouverture`, `date_cloture` (aujourd'hui), `duree_jours`, `effectif_initial`, `effectif_final`, `morts_et_pertes`, `taux_survie` ; si `cloturable` est faux, afficher `raison_refus` et désactiver « OK » ;
   - « OK » → `POST /{id}/cloturer/` (corps vide) → réponse : le traitement (`statut: "CLOTUREE"`, `effectif_final`, `hapas`…), `message`, `mise_en_stock_possible: true` ;
   - dialogue « Mettre en stock maintenant ? » : **Oui** → formulaire de mise en stock (4.3) prérempli avec le lot ; **Non** → le lot reste `EN_TRAITEMENT`, ses hapas sont bloqués (aucune mortalité ni perte) jusqu'à la mise en stock.

### 4.3 Mise en stock (`/api/v1/stocks/`) — règles : section 5
- `POST /` `{id_lot, id_bassin, finalite: "GROSSISSEMENT" | "VENTE", date_mise_en_stock, justification_ecart, observation, hapas: [{id_hapa, effectif_entrant}]}`.
- Afficher la référence d'effectif (effectif final du traitement, ou quantité initiale du lot sans traitement) : l'effectif entrant ne la dépasse jamais ; tout écart exige `justification_ecart`.
- Pas de modification ni de suppression depuis l'interface (section 6). Le stock est vidé uniquement par la sortie vers le grossissement (4.7).

### 4.4 Registre (`/api/v1/effectifs/`)
- `GET solde/?bassin=&espece=&date=` → `{bassin, reference_bassin, espece, date, effectif_vivant}`.
- `GET par-hapa/?bassin=&date=` → effectifs par hapa.
- `GET /?bassin=&espece=&date_evenement=` → mouvements (lecture seule).
- `POST /{id}/annuler/` (A, G) `{motif_annulation, date_annulation?}`.

### 4.5 Mortalité et perte (`/api/v1/effectifs/evenements-mortalite-perte/`) — section 6
- `POST /` `{type_evenement: "MORTALITE" | "PERTE", id_bassin, id_hapa (traitement, stock), espece, quantite, date_evenement, id_cause (mortalité) | id_categorie + justification (perte), observation}`.
- Listes : `GET /api/v1/effectifs/causes-mortalite/?actif=true`, `GET /api/v1/effectifs/categories-perte/`.
- Totaux : `GET /totaux/`. Annulation (A, G) : `POST /{id}/annuler/` `{motif}`.
- Refus à prévoir : hapa bloqué (traitement clôturé), lot de grossissement clôturé (registre figé), effectif insuffisant.

### 4.6 Provende (`/api/v1/provende/`) — section 7
- Référentiels : `GET types/`, `GET /` (provendes).
- Distribution : `POST consommations-provende/` `{id_bassin, id_hapa (traitement, stock), id_provende, date_distribution, quantite_kg, observation}` ; au plus 3 par jour et par cible.
- `GET consommations-provende/totaux-journaliers/`, `GET consommations-provende/jours-manquants/` ; annulation (A, G) `POST consommations-provende/{id}/annuler/` `{motif}`.
- En grossissement : refus si aucun lot en cours ou si la date tombe dans la période d'un lot clôturé.

### 4.7 Grossissement (`/api/v1/grossissement/`) — `CONTEXTE_INDICATEURS_GROSSISSEMENT.md`
**Sortie du stock et création du lot** (A, G), un écran en trois blocs sans brouillon :
1. bloc sortie : lot en stock (finalité `GROSSISSEMENT`) et bassin de destination ;
2. bloc pesée initiale : `date_pesee_initiale`, `pourcentage_echantillon` (10), `nombre_poissons_peses`, `poids_total_g` ;
3. bloc lot : `code` (prérempli avec `code_propose`), `date_mise_en_place`, `effectif_entrant`, `justification_ecart`, `observation`.
- À chaque changement : `POST lots-grossissement/apercu-sortie/` (mêmes champs, facultatifs) → valeurs calculées et erreurs, rien n'est écrit.
- « Valider » : `POST lots-grossissement/` → 201 avec le lot. « Annuler » : rien à appeler.
- Échantillon sous la cible : 400 `{"echantillon_non_conforme": message, "lignes_non_conformes": [...]}` → demander confirmation, puis renvoyer avec `confirmer_echantillon_non_conforme: true`.

**Fiche et liste des lots** : `GET lots-grossissement/?statut=ACTIF|CLOTURE&espece=&bassin=&date_min=&date_max=`, `GET lots-grossissement/{id}/` (dont `poids_moyen_actuel`, `derniers_indicateurs`, `prochaine_echeance_pesee`, `pesee_en_retard`, `evacuation_requise`), `GET lots-grossissement/{id}/origine/` (parcours du lot d'origine), `PATCH lots-grossissement/{id}/` `{observation, pourcentage_groupe_pesee}` (A, G, lot actif).

**Pesée** (depuis la fiche du lot) :
- formulaire : `POST pesees/apercu/` `{id_lot_grossissement, date?, nombre_poissons_peses?, poids_total_g?}` → effectif vivant, cible, poids moyen, pourcentage réel, dernière pesée, date minimale ;
- enregistrer : `POST pesees/` `{id_lot_grossissement, date, nombre_poissons_peses, poids_total_g, pourcentage_echantillon?, observation?}` (même mécanisme de confirmation que P0) ;
- corriger la dernière pesée (A, G) : `PATCH pesees/{id}/` `{nombre_poissons_peses?, poids_total_g?, pourcentage_echantillon?, observation?}` (date figée) ;
- annuler la dernière pesée (A, G) : `POST pesees/{id}/annuler/` `{motif}` ;
- liste : `GET pesees/?lot=&bassin=&statut=&date_min=&date_max=`.

**Suivi de cycle** : `GET lots-grossissement/{id}/suivi/` — contrat et normes de la page dans `CONTEXTE_PHASE4_PAGE_SUIVI.md`, données factices dans `docs/contrats/`.

**Clôture** (A, G) — `CONTEXTE_CLOTURE_LOT.md` :
1. lien « Clôturer le lot » (si `seuil.fin_cycle_possible`) → `GET lots-grossissement/{id}/apercu-cloture/` → formulaire en lecture seule (date, effectifs initial et final, poids moyens initial et final, écart %) ; si `cloturable` est faux, afficher `raison_refus` (par exemple « faire une pesée finale ») ;
2. « Valider » → `POST lots-grossissement/{id}/cloturer/` `{observation?}` → lot clôturé + `message` + `evacuation_requise` ;
3. dialogue « Évacuer le bassin ? » : **Oui** → `POST lots-grossissement/{id}/evacuer/` ; **Non** → rien ; l'action reste disponible sur la fiche tant que `evacuation_requise` est vrai.

**Historique des clôtures** : `GET lots-grossissement/historique-clotures/?espece=&bassin=&date_min=&date_max=` ; le lien « détails » ouvre le suivi du lot en lecture seule.

**Annuler une sortie vers le grossissement** (A, G, correction d'erreur) : `POST /api/v1/effectifs/sorties-stock/{id}/annuler/` `{motif}` ; refusée dès que le lot a une pesée ou est clôturé.

## 5. Données de test

- `docs/contrats/*.exemple.json` : réponses réelles du suivi et de la liste des lots (données factices).
- `python manage.py seed_grossissement` : scénarios de grossissement simulés (préfixe `SIM-`).
- Collections Postman `tests/postman/` (voir `README_TEST.md`) : exemples de requêtes et de réponses pour chaque parcours.

## 6. Points d'attention (état du backend)

1. `PATCH /api/v1/lots/{id}/cloturer/` est bloquée (404) depuis le 2026-11-17 : elle forçait `CLOTUREE` sans contrôle. Un lot perdu avant le grossissement (traitement ou stock à effectif nul) n'a pas encore de fin de vie.
2. L'Employé peut **créer** une mise en stock mais ni la lister ni la lire (403) : prévoir l'écran en conséquence ou demander l'ouverture de la lecture.
3. `/api/v1/environnement/` exige depuis le 2026-11-17 un utilisateur connecté et actif (401 sans jeton) : les trois rôles lisent, saisissent et modifient ; seul l'Administrateur supprime une mesure (403 sinon). Envoyer le jeton sur tous ses appels.
4. `DELETE /api/v1/stocks/{id}/` et `DELETE /api/v1/traitement/{id}/` sont des outils de test : ne pas les exposer.
5. Une mise en stock n'est pas modifiable en pratique (refus dès qu'elle a des mouvements) : ne pas proposer de modification.
6. La sortie de stock à finalité `VENTE` n'existe pas : un lot en stock « vente » ne sort pas.
7. Les routes d'authentification sont aussi exposées sous `/api/v1/auth/auth/…` (inclusion en double) : utiliser `/api/auth/…`.
8. Durée de vie du jeton d'accès (15 jours) supérieure à celle du refresh (7 jours) : réglage à corriger côté backend.
