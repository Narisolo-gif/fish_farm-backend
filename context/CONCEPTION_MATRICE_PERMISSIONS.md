# Conception : matrice des permissions et des accès

Statut : **implémentée** le 2026-11-17 (plan : `PLAN_MATRICE_PERMISSIONS.md`). Écart à la section 4 corrigé pendant l'exécution : `lots` suppression → PILOTAGE (section 3). Méthodes exclues d'un viewset → 405 ; `OPTIONS` (métadonnées) → tout utilisateur actif.

## 1. Intention

Les droits de l'API ont été écrits module par module, à des moments différents. Ils sont incohérents : par exemple, le Gestionnaire crée une reproduction mais pas ses pontes, et l'Employé crée une éclosion sans pouvoir lire les pontes. Ils ne suivent pas non plus l'organisation réelle de la ferme. L'objectif est une **matrice unique, alignée sur qui fait quoi sur l'exploitation**, appliquée de la même façon partout et vérifiée par un test.

Organisation réelle (petite exploitation) :
- le **directeur des opérations** décide de tout ce qui est technique, matériel et financier, et il est le **seul à réparer une erreur**. En son absence, tout attend son retour : il n'y a pas de suppléant ;
- la **gérante** gère l'administratif, les RH, les ventes et la relation client, sans intervenir dans la production ;
- les **employés** sont sur le terrain : ils collectent les données, nourrissent les poissons, et surveillent les pertes et les paramètres environnementaux. Peu qualifiés techniquement, ils **rapportent** au directeur les événements qui démarrent ou terminent une étape, et c'est lui qui les saisit et les valide ;
- le **développeur** garde un compte de support qui a accès à tout, pour les incidents.

## 2. Rôles

| Rôle (libellé en base) | Qui | Statut |
|---|---|---|
| `Administrateur` | développeur, support informatique | existant, conservé |
| `Directeur` | directeur des opérations | **à créer** |
| `Gestionnaire` | gérante | existant, change de sens |
| `Employé` | personnel de terrain | existant, conservé |

Les libellés sont gardés tels quels : renommer un rôle toucherait toutes les permissions et les comptes existants. Les libellés affichés à l'écran peuvent différer (« Directeur des opérations », « Responsable administrative », « Technicien »), c'est le choix du frontend.

## 3. Matrice validée

A = Administrateur, D = Directeur, G = Gestionnaire, E = Employé. « lecture » = liste, détail et actions de consultation.

| Domaine | A | D | G | E |
|---|:-:|:-:|:-:|:-:|
| Comptes utilisateurs (création, rôle, désactivation, mot de passe) | ✓ | lecture | ✓ | — |
| Référentiels : bassins, hapas, provendes et types, causes de mortalité, catégories de perte | ✓ | ✓ | lecture | lecture |
| Démarrer ou terminer un événement : reproduction, ponte, éclosion, lot, traitement (ouverture, clôture), mise en stock, sortie vers le grossissement, clôture et évacuation d'un lot de grossissement | ✓ | ✓ | lecture | lecture |
| Faits de terrain : mortalité, perte, distribution de provende, paramètres environnementaux, pesée de suivi (saisie) | ✓ | ✓ | lecture | ✓ |
| Corrections, annulations, suppressions | ✓ | ✓ | — | — |
| Suivi, historique, indicateurs, registre (lecture) | ✓ | ✓ | ✓ | ✓ |

Règles transverses :
- un utilisateur non connecté reçoit 401 ; un compte désactivé (`is_active` faux), au statut autre que `ACTIF` ou sans rôle reçoit 403, **partout** ;
- **refus par défaut** : une action absente de la matrice est refusée ;
- l'Administrateur saisit sous son propre nom, tracé comme les autres.

## 4. Détail par route (ce que l'implémentation applique)

Groupes : `TOUS` = {A, D, G, E} ; `PILOTAGE` = {A, D} ; `TERRAIN` = {A, D, E} ; `COMPTES` = {A, G}.

| Ressource (routes) | Lecture | Écriture |
|---|---|---|
| `roles` (`/api/roles/`) | TOUS* | création, modification, suppression : A |
| `utilisateurs` (`/api/users/`) | A, D, G | création, modification, suppression, `reset-password` : COMPTES, avec un garde-fou (section 6) : seul A attribue le rôle `Administrateur` ou agit sur un compte Administrateur ; `change-password` (son propre compte) : tout utilisateur actif |
| `me`, `logout` | tout utilisateur actif | — |
| `bassins`, `hapas` (les deux jeux de routes) | TOUS | création, modification, suppression : PILOTAGE |
| `types-provende`, `provendes` | TOUS | PILOTAGE |
| `causes-mortalite`, `categories-perte` | TOUS | PILOTAGE |
| `reproductions`, `pontes` | TOUS | création, modification, `desactiver`, suppression : PILOTAGE |
| `eclosions` (dont `getbyref_pontes`, `getbydateEclosion`) | TOUS | création, modification, `desactiver`, suppression : PILOTAGE |
| `lots` | TOUS | création, modification, suppression : PILOTAGE |
| `traitement` (dont `effectifs`, `apercu-cloture`) | TOUS | création, `cloturer`, modification, suppression : PILOTAGE |
| `stocks` | TOUS | création, modification : PILOTAGE ; suppression (outil de test) : A |
| `mouvements` (dont `solde`, `par-hapa`) | TOUS | `annuler` : PILOTAGE |
| `evenements-mortalite-perte` (dont `totaux`) | TOUS | création : TERRAIN ; `annuler` : PILOTAGE |
| `consommations-provende` (dont `totaux-journaliers`, `jours-manquants`) | TOUS | création : TERRAIN ; `annuler` : PILOTAGE |
| `environnement` (dont `bassin/{id}`, `bassin/{id}/latest`, `latest`) | TOUS | création : TERRAIN ; modification, suppression : PILOTAGE |
| `sorties-stock` | TOUS | `annuler` : PILOTAGE |
| `lots-grossissement` (dont `suivi`, `origine`, `apercu-cloture`, `historique-clotures`) | TOUS | `apercu-sortie`, création (sortie), modification, `cloturer`, `evacuer` : PILOTAGE |
| `pesees` (dont `apercu`) | TOUS | création, `apercu` : TERRAIN ; modification, `annuler` : PILOTAGE |

\* `roles` en lecture pour tous : le frontend affiche le libellé du rôle.

## 5. Approches considérées

- **A. Matrice centrale (retenue)** : la matrice est déclarée en un seul endroit, une seule classe de permission la lit, et un test la vérifie route par route. Les incohérences disparaissent et un changement de droit ne touche qu'une ligne. En contrepartie, il faut retoucher la quinzaine de viewsets et retirer les classes propres à chaque module.
- B. Retouche module par module : chaque modification reste petite, mais la dispersion à l'origine des incohérences reste en place.
- C. Groupes et permissions de Django : ce mécanisme ne connaît que le CRUD par modèle (il faudrait des permissions sur mesure pour clôturer, évacuer, annuler), il faudrait refondre les rôles, et une erreur dans l'administration pourrait ouvrir des droits.

## 6. Conception de l'approche A

- **Garde-fou des comptes** : la matrice ne connaît que l'action, pas le compte visé. Dans `UtilisateurViewSet`, une vérification supplémentaire refuse (403) à un non-Administrateur : la création ou la modification d'un compte avec le rôle `Administrateur`, et toute modification, suppression ou `reset-password` d'un compte qui a déjà ce rôle. La gérante gère librement les comptes `Directeur`, `Gestionnaire` et `Employé`.
- **Rôles** : constantes des quatre libellés dans `apps/core/` (une seule source, plus aucun libellé écrit en dur dans les viewsets).
- **Matrice** : un dictionnaire `{ressource: {action: rôles autorisés}}` dans `apps/core/permissions.py`, construit avec les groupes `TOUS`, `PILOTAGE`, `TERRAIN`, `COMPTES`. Les noms d'action sont ceux de DRF (`list`, `retrieve`, `create`, `update`, `partial_update`, `destroy`) et ceux des actions personnalisées (`cloturer`, `annuler`, `apercu_cloture`…).
- **Classe `PermissionMatrice`** : elle contrôle l'utilisateur (connecté, `is_active`, statut `ACTIF`, rôle présent), puis cherche `view.ressource` et `view.action` dans la matrice. Une action absente est refusée. Si `view.action` vaut `None` (méthode non routée), la classe laisse passer pour que DRF réponde 405, comme aujourd'hui (`DELETE` d'une pesée → 405).
- **Viewsets** : chacun déclare `ressource = "…"` et `permission_classes = [PermissionMatrice]`. Les `get_permissions` actuels et les classes propres aux modules disparaissent (`StockPermission`, `HapaPermission`, permissions des lots, de l'éclosion et des effectifs, `IsAdministrateur`…). Les vues-fonctions `me` et `logout` gardent une permission « utilisateur actif ».
- **Migration de données** (`core`) : `get_or_create` des quatre rôles, dont `Directeur`. Une base neuve a ainsi ses rôles ; une base existante reçoit seulement `Directeur`.
- **Comptes existants** : aucune réaffectation automatique. Après le déploiement, l'Administrateur crée ou réaffecte le compte du directeur (`Directeur`) ; `gestionnaire_demo` n'a plus de droits d'écriture en production.
- **Tests** :
  - un test parcourt la matrice et vérifie, pour chaque ressource, chaque action et chaque rôle, le code attendu (200/201/204 contre 403), plus 401 sans connexion et 403 pour un compte `INACTIF` ;
  - les tests existants qui écrivent en production avec un compte Gestionnaire passent au rôle Directeur.
- **Postman** :
  - ajouter les identifiants d'un compte `directeur_demo` à l'environnement ;
  - les requêtes d'écriture de production faites avec le jeton gestionnaire passent au jeton directeur ;
  - ajouter quelques requêtes de refus (gérante en écriture de production, employé en correction).
- **Documentation** : `GUIDE_INTEGRATION_FRONTEND.md` (matrice des droits), `REGLES_DE_GESTION_FISH_FARM.md` (section 1), `CLAUDE.md`.

## 7. Objectifs, non-objectifs, anti-objectifs

- Objectifs : appliquer la matrice de la section 3 partout ; un seul contrôle du compte inactif ; une matrice lisible en un endroit et testée.
- Non-objectifs :
  - renommer les rôles en base ;
  - droits « par objet » (par exemple, un employé limité à ses bassins) ;
  - journal d'audit ;
  - écrans du frontend ;
  - politique de mot de passe.
- Anti-objectifs (ce qui serait un échec même si « ça marche ») :
  - un rôle qui gagne un droit sans figurer dans la matrice ;
  - une route oubliée qui reste ouverte (d'où le refus par défaut et le test exhaustif) ;
  - une règle métier qui change en même temps que les droits.

## 8. Décisions prises pendant la conception

1. **Attribution des rôles** : la gérante crée, modifie et désactive les comptes `Directeur`, `Gestionnaire` et `Employé` ; seul l'Administrateur attribue le rôle `Administrateur` ou modifie un compte Administrateur.
2. **Paramètres environnementaux** : la matrice prévaut sur la règle précédente. L'employé crée ; le directeur et l'administrateur modifient et suppriment (la gérante lit).
3. **Suppression d'une mise en stock** (outil de test) : Administrateur seul.
4. Choix techniques : rôles lisibles par tous (affichage du libellé) ; refus par défaut d'une action absente de la matrice ; une méthode non routée répond toujours 405.

## 9. Choix laissés à l'implémentation

Nom exact du module de constantes, organisation des tests (un fichier par app ou un test central), ordre de migration des viewsets.
