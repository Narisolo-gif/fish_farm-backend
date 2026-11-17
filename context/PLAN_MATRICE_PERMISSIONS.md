# Matrice des permissions — plan d'implémentation

> **Pour les agents :** sous-compétence requise : superpowers:subagent-driven-development (recommandée) ou superpowers:executing-plans, tâche par tâche. Les étapes utilisent des cases à cocher (`- [ ]`).

**Objectif :** appliquer partout la matrice de droits validée (Administrateur, Directeur, Gestionnaire, Employé) par une seule classe de permission, vérifiée par des tests.

**Architecture :** la matrice `{ressource: {action: rôles}}` est déclarée dans `apps/core/permissions.py`. Une classe `PermissionMatrice` contrôle l'utilisateur (connecté, actif, statut `ACTIF`, rôle) puis cherche `view.ressource` et `view.action` dans la matrice ; une action absente est refusée. Chaque viewset déclare sa `ressource` et utilise `PermissionMatrice`. Un garde-fou propre aux comptes empêche un non-Administrateur de toucher au rôle `Administrateur`.

**Stack :** Django 5.2, Django REST Framework, PostgreSQL (tests sur base `test_`), Postman/newman.

**Spécification :** `context/CONCEPTION_MATRICE_PERMISSIONS.md` (sections 3, 4, 6 et 8 font foi).

## Contraintes globales

- Libellés de rôle exacts en base : `Administrateur`, `Directeur`, `Gestionnaire`, `Employé` (aucun renommage).
- Groupes : `TOUS` = {A, D, G, E} ; `PILOTAGE` = {A, D} ; `TERRAIN` = {A, D, E} ; `COMPTES` = {A, G}.
- Sans connexion : 401. Compte `is_active` faux, statut autre que `ACTIF` ou sans rôle : 403, partout.
- Refus par défaut : une action absente de la matrice est refusée (403). `view.action` à `None` (méthode non routée) : laisser DRF répondre 405.
- Seul l'Administrateur attribue le rôle `Administrateur` ou agit (modification, suppression, `reset-password`) sur un compte Administrateur.
- Aucun changement de règle métier : seules les permissions changent.
- Français pour les noms, messages et commentaires. Aucun libellé de rôle écrit en dur hors de `apps/core/roles.py`.
- Pas de `migrate` sur la base de développement, pas de commit : le développeur applique et commite. Tests : `docker compose exec backend python manage.py test apps`.

## Points de vigilance (à couvrir par les tests)

1. **Compte au statut `INACTIF` mais `is_active` vrai** : refusé (403) sur toutes les routes, y compris celles qui ne vérifiaient que `is_active` (reproduction, pontes, clôture de traitement). Test : tâche 1.
2. **Gérante qui modifie son propre compte pour prendre le rôle `Administrateur`** (`PATCH /api/users/{son_id}/`) : 403 et rôle inchangé. Test : tâche 2.
3. **Méthode non routée** (`DELETE /api/v1/grossissement/pesees/{id}/`, `DELETE …/lots-grossissement/{id}/`) : toujours 405, jamais 403. Test : tâche 7.
4. **Action ajoutée plus tard à un viewset sans entrée dans la matrice** : refusée, et le test structurel échoue en nommant l'action. Test : tâche 8.
5. **Utilisateur sans rôle** (`role` nul) : 403, sans erreur 500. Test : tâche 1.

---

### Tâche 1 : socle (rôles, matrice, `PermissionMatrice`, migration du rôle Directeur)

**Fichiers :**
- Créer : `apps/core/roles.py`
- Modifier : `apps/core/permissions.py` (ajouts ; les anciennes classes restent jusqu'à la tâche 9)
- Créer : `apps/core/migrations/00XX_roles_metier.py` (numéro suivant la dernière migration de `core`)
- Créer : `apps/core/tests/__init__.py`, `apps/core/tests/test_permission_matrice.py`

**Interfaces :**
- Produit : `apps/core/roles.py` : `ADMINISTRATEUR = "Administrateur"`, `DIRECTEUR = "Directeur"`, `GESTIONNAIRE = "Gestionnaire"`, `EMPLOYE = "Employé"`, `ROLES = (ADMINISTRATEUR, DIRECTEUR, GESTIONNAIRE, EMPLOYE)`, `TOUS`, `PILOTAGE`, `TERRAIN`, `COMPTES` (`frozenset[str]`).
- Produit : `apps/core/permissions.py` : `MATRICE: dict[str, dict[str, frozenset[str]]]` ; `role_actif(user) -> str | None` (libellé du rôle si l'utilisateur est connecté, `is_active`, statut `ACTIF` et a un rôle, sinon `None`) ; `class PermissionMatrice(BasePermission)` ; `class UtilisateurActif(BasePermission)` (vrai si `role_actif(user)` n'est pas `None`).

- [ ] **Étape 1 : écrire les tests qui échouent** dans `test_permission_matrice.py` (`SimpleTestCase` ou `TestCase`, vue factice `type("V", (), {"ressource": "traitement", "action": …})`, requête via `APIRequestFactory`) :
  - `test_refus_sans_connexion` : `AnonymousUser` → `has_permission` faux ;
  - `test_refus_compte_inactif_ou_sans_role` : statut `INACTIF` avec `is_active` vrai → faux ; `is_active` faux → faux ; `role=None` → faux, sans exception ;
  - `test_action_autorisee_selon_le_role` : `traitement.cloturer` vrai pour A et D, faux pour G et E ; `traitement.list` vrai pour les quatre ;
  - `test_refus_par_defaut` : ressource inconnue → faux ; action inconnue (`"action_inexistante"`) → faux ;
  - `test_methode_non_routee_laisse_passer` : `action=None` → vrai (DRF répondra 405) ;
  - `test_roles_metier_crees` : après les migrations, `Role.objects.filter(libelle__in=ROLES).count() == 4`.
- [ ] **Étape 2 : lancer les tests et vérifier l'échec.** `docker compose exec backend python manage.py test apps.core.tests.test_permission_matrice` → ImportError (`apps.core.roles` absent).
- [ ] **Étape 3 : écrire `roles.py`, `role_actif`, `UtilisateurActif`, `PermissionMatrice`, puis `MATRICE`** à partir de la section 4 de la conception. Contenu exact de `MATRICE`, avec `L` = `{list, retrieve}` → TOUS :

| Ressource | Actions → rôles |
|---|---|
| `roles` | L → TOUS ; `create`, `update`, `partial_update`, `destroy` → {A} |
| `utilisateurs` | `list`, `retrieve` → {A, D, G} ; `create`, `update`, `partial_update`, `destroy`, `reset_password` → COMPTES ; `change_password` → TOUS |
| `bassins`, `hapas`, `types_provende`, `provendes`, `causes_mortalite`, `categories_perte` | L → TOUS ; `create`, `update`, `partial_update`, `destroy` → PILOTAGE |
| `reproductions`, `pontes` | L → TOUS ; `create`, `update`, `partial_update`, `destroy`, `desactiver` → PILOTAGE |
| `eclosions` | L, `getbyref_pontes`, `getbydateEclosion` → TOUS ; `create`, `update`, `partial_update`, `destroy`, `desactiver` → PILOTAGE |
| `lots` | L → TOUS ; `create`, `update`, `partial_update`, `destroy` → PILOTAGE (ajout pendant l'exécution, voir le journal) |
| `traitement` | L, `effectifs`, `apercu_cloture` → TOUS ; `create`, `cloturer`, `update`, `partial_update`, `destroy` → PILOTAGE |
| `stocks` | L → TOUS ; `create`, `update`, `partial_update` → PILOTAGE ; `destroy` → {A} |
| `mouvements` | L, `solde`, `par_hapa` → TOUS ; `annuler` → PILOTAGE |
| `evenements_mortalite_perte` | L, `totaux` → TOUS ; `create` → TERRAIN ; `annuler` → PILOTAGE |
| `consommations_provende` | L, `totaux_journaliers`, `jours_manquants_action` → TOUS ; `create` → TERRAIN ; `annuler` → PILOTAGE |
| `environnement` | L, `historique_bassin`, `derniere_mesure_bassin`, `dernieres_mesures` → TOUS ; `create` → TERRAIN ; `update`, `partial_update`, `destroy` → PILOTAGE |
| `sorties_stock` | L → TOUS ; `annuler` → PILOTAGE |
| `lots_grossissement` | L, `suivi`, `origine`, `apercu_cloture`, `historique_clotures` → TOUS ; `apercu_sortie`, `create`, `partial_update`, `cloturer`, `evacuer` → PILOTAGE |
| `pesees` | L → TOUS ; `create`, `apercu` → TERRAIN ; `partial_update`, `annuler` → PILOTAGE |

  Migration : `RunPython` qui fait `get_or_create(libelle=…)` pour les quatre libellés de `roles.ROLES` (retour arrière : `noop`, sans rien supprimer).
- [ ] **Étape 4 : lancer les tests et vérifier qu'ils passent**, ainsi que `python manage.py makemigrations --check` (aucun changement).
- [ ] **Étape 5 : pas de commit** (le développeur commite).

### Tâche 2 : comptes (rôles, utilisateurs, `me`, `logout`) et garde-fou Administrateur

**Fichiers :**
- Modifier : `apps/core/views.py`
- Créer : `apps/core/tests/test_comptes.py`

**Interfaces :**
- Consomme : `PermissionMatrice`, `UtilisateurActif`, `roles.ADMINISTRATEUR` (tâche 1).
- Produit : `RoleViewSet.ressource = "roles"`, `UtilisateurViewSet.ressource = "utilisateurs"` ; `UtilisateurViewSet._refuser_si_compte_administrateur(cible=None, role_demande=None) -> None` (lève `PermissionDenied` si l'utilisateur courant n'est pas Administrateur et que `cible` a le rôle Administrateur ou que `role_demande` est le rôle Administrateur).

- [ ] **Étape 1 : écrire les tests qui échouent** :
  - `test_gerante_gere_les_comptes_non_administrateurs` : G crée un compte `Employé` (201), le passe à `Directeur` (200), le désactive (200), réinitialise son mot de passe (200) ;
  - `test_gerante_ne_touche_pas_au_role_administrateur` : G crée un compte `Administrateur` → 403 ; `PATCH` d'un compte Administrateur → 403 ; `reset-password` d'un compte Administrateur → 403 ; G passe **son propre** compte à `Administrateur` → 403, rôle inchangé en base ;
  - `test_administrateur_attribue_le_role_administrateur` : A crée un compte `Administrateur` → 201 ;
  - `test_directeur_lit_les_comptes_sans_les_modifier` : D liste → 200, crée → 403 ; E liste → 403 ;
  - `test_roles_lisibles_par_tous` : E `GET /api/roles/` → 200, `POST` → 403 ;
  - `test_me_et_change_password` : un E actif → 200 sur `/api/auth/me/` et sur `change-password` ; un compte `INACTIF` → 403 sur `/api/auth/me/`.
- [ ] **Étape 2 : lancer les tests et vérifier l'échec** (`python manage.py test apps.core`).
- [ ] **Étape 3 : implémenter.**
  - `permission_classes = [PermissionMatrice]` et `ressource` sur les deux viewsets.
  - `change_password` garde `permission_classes=[UtilisateurActif]` ; `reset_password` n'a plus de `permission_classes` propre (c'est la matrice qui décide).
  - `me` et `logout` utilisent `UtilisateurActif`.
  - Le garde-fou est appelé dans `perform_create`, `perform_update`, `perform_destroy` et `reset_password`, avec le rôle demandé lu dans le champ d'écriture du rôle de `UtilisateurSerializer`.
- [ ] **Étape 4 : lancer les tests et vérifier qu'ils passent.**
- [ ] **Étape 5 : pas de commit.**

### Tâche 3 : référentiels (bassins, hapas, provendes, causes, catégories)

**Fichiers :**
- Modifier : `apps/bassins/viewsets.py` (`BassinViewSet`, `HapaViewSet`, `HapaGestionViewSet` ; supprimer `HapaPermission`), `apps/provende/viewsets.py` (`ProvendeViewSet`, `TypeProvendeViewSet`), `apps/effectifs/viewsets.py` (`CauseMortaliteViewSet`, `CategoriePerteViewSet`)
- Créer : `apps/core/tests/test_droits_referentiels.py`

**Interfaces :** consomme `PermissionMatrice` ; ressources `bassins`, `hapas` (les deux viewsets de hapas), `provendes`, `types_provende`, `causes_mortalite`, `categories_perte`.

- [ ] **Étape 1 : écrire les tests qui échouent** :
  - `test_directeur_ecrit_les_referentiels` : D crée un bassin, un hapa (`/api/v1/bassins/hapas/`), une provende, une cause → 201 ;
  - `test_gerante_et_employe_lisent_seulement` : G et E listent (200) ; G et E créent un bassin ou une cause → 403.
- [ ] **Étape 2 : vérifier l'échec** (le Directeur reçoit 403 aujourd'hui).
- [ ] **Étape 3 : remplacer chaque `get_permissions` ou `permission_classes` par `permission_classes = [PermissionMatrice]` et `ressource`.**
- [ ] **Étape 4 : lancer les tests de la tâche et ceux de `apps.bassins`, `apps.provende` et `apps.effectifs` ; tous doivent passer.**
- [ ] **Étape 5 : pas de commit.**

### Tâche 4 : chaîne de démarrage (reproductions, pontes, éclosions, lots)

**Fichiers :**
- Modifier : `apps/reproduction/viewsets.py`, `apps/ecloserie/viewsets.py` (supprimer `IsRoleEcloserieAutorise`, `IsAdministrateurOuGestionnaireEcloserie`), `apps/lots/viewsets.py` (supprimer `IsRoleAutorisePourLectureLots`, `IsAdministrateurOuGestionnaireLots` ; le bloc commenté `cloturer` reste commenté)
- Créer : `apps/core/tests/test_droits_chaine.py`

**Interfaces :** ressources `reproductions`, `pontes`, `eclosions`, `lots`.

- [ ] **Étape 1 : écrire les tests qui échouent** :
  - `test_directeur_cree_reproduction_ponte_eclosion_lot` : D crée la chaîne complète → 201 à chaque étape ;
  - `test_gerante_et_employe_lisent_la_chaine_sans_ecrire` : G et E listent les quatre ressources (200) ; G crée une reproduction → 403 ; E crée une éclosion → 403 ;
  - `test_desactivation_reservee_au_pilotage` : G `PATCH …/reproductions/{id}/desactiver/` → 403 ; D → 200.
- [ ] **Étape 2 : vérifier l'échec.**
- [ ] **Étape 3 : implémenter** (`ressource` et `PermissionMatrice` sur les quatre viewsets).
- [ ] **Étape 4 : lancer les tests de la tâche et ceux de `apps.lots` ; tous doivent passer.**
- [ ] **Étape 5 : pas de commit.**

### Tâche 5 : traitement et mise en stock

**Fichiers :**
- Modifier : `apps/traitement/viewsets.py`, `apps/stocks/viewsets.py` (supprimer `StockPermission`), `apps/traitement/tests/test_cloture.py`
- Créer : `apps/core/tests/test_droits_traitement_stock.py`

**Interfaces :** ressources `traitement`, `stocks`.

- [ ] **Étape 1 : écrire les tests qui échouent et adapter l'existant** :
  - `test_ouverture_et_cloture_reservees_au_directeur` : E et G ouvrent un traitement → 403 ; D → 201 ; E clôture → 403 ; D → 200 ;
  - `test_tous_lisent_traitement_et_stock` : E liste et lit une mise en stock → 200 (aujourd'hui 403) ;
  - `test_mise_en_stock_par_le_directeur_suppression_par_l_admin` : E crée → 403 ; D crée → 201 ; D supprime → 403 ; A supprime → 204 ;
  - dans `apps/traitement/tests/test_cloture.py`, `test_cloture_reservee_admin_et_gestionnaire` devient `test_cloture_reservee_au_pilotage` : la clôture par `Gestionnaire` attend 403, par `Directeur` 200 ; `test_employe_garde_la_creation_et_la_lecture` devient `test_employe_lit_sans_creer` (création → 403).
- [ ] **Étape 2 : vérifier l'échec.**
- [ ] **Étape 3 : implémenter.**
- [ ] **Étape 4 : lancer `apps.traitement`, `apps.stocks` et la nouvelle classe ; tous doivent passer.**
- [ ] **Étape 5 : pas de commit.**

### Tâche 6 : journaux de terrain (mouvements, mortalités et pertes, provende, environnement, sorties de stock)

**Fichiers :**
- Modifier : `apps/effectifs/viewsets.py` (`MouvementEffectifViewSet`, `EvenementMortalitePerteViewSet`, `SortieStockViewSet` ; supprimer `IsRoleEffectifAutorise`, `IsAdministrateurOuGestionnaireEffectif`), `apps/provende/viewsets.py` (`ConsommationProvendeViewSet`), `apps/environnement/viewsets.py`, `apps/environnement/tests/test_permissions.py`
- Créer : `apps/core/tests/test_droits_journaux.py`

**Interfaces :** ressources `mouvements`, `evenements_mortalite_perte`, `consommations_provende`, `environnement`, `sorties_stock`.

- [ ] **Étape 1 : écrire les tests qui échouent et adapter l'existant** :
  - `test_employe_saisit_les_faits_de_terrain` : E crée une mortalité, une distribution et une mesure environnementale → 201 ;
  - `test_gerante_ne_saisit_pas` : G crée une mortalité → 403 ;
  - `test_corrections_reservees_au_pilotage` : E et G annulent une mortalité ou une distribution → 403 ; D → 200 ;
  - environnement : `test_utilisateurs_connectes_et_actifs_acceptes` attend la modification par E → 403 et par D → 200 ; `test_suppression_reservee_a_l_administrateur` devient `test_suppression_reservee_au_pilotage` (G et E → 403, D → 204).
- [ ] **Étape 2 : vérifier l'échec.**
- [ ] **Étape 3 : implémenter** (mettre à jour la docstring de `ParametreEnvironnementalViewSet` avec la nouvelle règle).
- [ ] **Étape 4 : lancer `apps.effectifs`, `apps.provende`, `apps.environnement` et la nouvelle classe ; tous doivent passer.**
- [ ] **Étape 5 : pas de commit.**

### Tâche 7 : grossissement (lots de grossissement, pesées)

**Fichiers :**
- Modifier : `apps/grossissement/viewsets.py`
- Créer : `apps/core/tests/test_droits_grossissement.py`

**Interfaces :** ressources `lots_grossissement`, `pesees`.

- [ ] **Étape 1 : écrire les tests qui échouent** :
  - `test_sortie_cloture_evacuation_reservees_au_directeur` : G valide une sortie → 403 ; D → 201 ; G clôture → 403 ;
  - `test_employe_pese_sans_corriger` : E crée une pesée → 201 ; E `PATCH` ou `annuler` → 403 ; D → 200 ;
  - `test_methodes_non_routees_toujours_405` : `DELETE pesees/{id}/` et `DELETE lots-grossissement/{id}/` par A → 405.
- [ ] **Étape 2 : vérifier l'échec.**
- [ ] **Étape 3 : implémenter.**
- [ ] **Étape 4 : lancer `apps.grossissement` et la nouvelle classe ; tous doivent passer** (les tests existants s'authentifient en `Administrateur` ou en `Employé` pour la lecture et la pesée).
- [ ] **Étape 5 : pas de commit.**

### Tâche 8 : test structurel de couverture de la matrice

**Fichiers :**
- Créer : `apps/core/tests/test_matrice_couverture.py`

**Interfaces :** consomme `MATRICE`, `PermissionMatrice` ; parcourt `django.urls.get_resolver()`.

- [ ] **Étape 1 : écrire le test** `test_chaque_route_est_couverte_par_la_matrice`. Pour chaque motif d'URL sous `api/` dont le callback a un attribut `cls` (viewset DRF) :
  - la classe déclare `ressource`, présente dans `MATRICE` ;
  - `PermissionMatrice` est dans ses `permission_classes` ;
  - chaque action du mapping de la route (`callback.actions.values()`) est une clé de `MATRICE[ressource]`.

  En cas d'échec, le message nomme la classe et l'action manquante. Deuxième test, `test_matrice_conforme_a_la_conception` : un tableau `ATTENDU` recopié de la section 4 de la conception, indépendamment de `MATRICE`, est comparé à `MATRICE` pour chaque (ressource, action). Troisième test, `test_chaque_role_sur_chaque_action` : pour chaque (ressource, action) d'`ATTENDU` et chacun des quatre rôles, `PermissionMatrice().has_permission` (utilisateur actif de ce rôle, vue factice) vaut vrai si et seulement si le rôle est attendu ; un compte `INACTIF` de chaque rôle donne faux partout. Les tâches 2 à 7 vérifient les codes HTTP sur les routes représentatives.
- [ ] **Étape 2 : le lancer** ; il doit passer si les tâches 2 à 7 sont complètes. Sinon, compléter le viewset ou la matrice concernés, sans élargir un droit.
- [ ] **Étape 3 : pas de commit.**

### Tâche 9 : nettoyage, suite complète, Postman et documentation

**Fichiers :**
- Modifier : `apps/core/permissions.py` (supprimer `IsAdministrateur`, `IsAdministrateurOuGestionnaire`, `IsTousRolesActifs` s'ils ne sont plus importés), `apps/grossissement/management/commands/seed_grossissement.py` (rôle via `roles.ADMINISTRATEUR`)
- Modifier : `tests/postman/pisciculture_local.postman_environment.json` (`director_username`, `director_password` pour `directeur_demo`), `tests/postman/pisciculture_e2e.postman_collection.json`, `tests/postman/grossissement_sortie_lot.postman_collection.json`, `tests/postman/README_TEST.md`
- Modifier : `context/GUIDE_INTEGRATION_FRONTEND.md` (section 3 remplacée par la matrice), `context/REGLES_DE_GESTION_FISH_FARM.md` (section 1, rôles), `CLAUDE.md` (stack et points connus), `context/CONCEPTION_MATRICE_PERMISSIONS.md` (statut : implémentée)

- [ ] **Étape 1 :** `grep` des anciennes classes et des libellés de rôle écrits en dur hors de `roles.py` et des tests ; les supprimer ou les remplacer.
- [ ] **Étape 2 :** `python manage.py check`, `makemigrations --check`, puis `python manage.py test apps` : tout doit passer.
- [ ] **Étape 3 : Postman.**
  - Ajouter une requête de connexion `directeur` au début des dossiers qui écrivent en production avec un jeton gestionnaire ou employé (`{{token_gestionnaire}}`, `{{ps_token_gestionnaire}}`, `{{sg_token_gestionnaire}}`, et les créations `{{token_employe}}` de traitement, stock ou éclosion) ; ces requêtes passent au jeton directeur ou attendent désormais 403.
  - Ajouter trois refus : gérante qui ouvre un traitement (403), employé qui annule une mortalité (403), gérante qui s'attribue le rôle Administrateur (403).
  - Vérifier la syntaxe JS de tous les scripts.
  - Dans `README_TEST.md`, ajouter le prérequis : créer `directeur_demo` (rôle `Directeur`, statut `ACTIF`) avec le compte Administrateur avant de lancer newman.
- [ ] **Étape 4 : documentation** (fichiers ci-dessus), en signalant dans `CLAUDE.md` que le compte du directeur doit être réaffecté au rôle `Directeur` après le `migrate`.
- [ ] **Étape 5 : pas de commit.**
