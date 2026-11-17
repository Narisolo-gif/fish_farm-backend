# Conception : collection Postman « flux complet »

Statut : **implémentée** le 2026-11-17 (`tests/postman/generer_flux_complet.py`, rejeu `apps/core/tests/test_collection_flux_complet.py`).

## 1. Intention

La collection `grossissement_sortie_lot` est devenue obsolète. Elle a été écrite au fil des refontes : ses dossiers recréent chacun leurs propres données, testent surtout des refus et jouent la plupart des écritures avec le compte administrateur. La collection `pisciculture_e2e` date, elle, d'avant la matrice des permissions, le suivi et la clôture.

La nouvelle collection a deux usages :
1. **Test fonctionnel de tous les flux** sous la nouvelle politique d'accès (`CONCEPTION_MATRICE_PERMISSIONS.md`) : chaque étape est jouée par le rôle qui la fait réellement, et des refus prouvent la matrice.
2. **Remplissage d'une base de démonstration** : les données créées restent en base et donnent des écrans réalistes au frontend (suivi, courbes, indicateurs, historique).

## 2. Objectifs, non-objectifs, anti-objectifs

- **Objectifs :**
  - parcours complet, dans l'ordre réel de l'exploitation, avec le jeton du bon rôle à chaque requête ;
  - refus de la matrice placés à côté de l'étape qu'ils protègent ;
  - base de démonstration riche à la fin ;
  - collection rejouable sur une base non vide.
- **Non-objectifs :**
  - remplacer les tests Django (les cas limites restent dans `apps/*/tests`) ;
  - remplacer la commande `seed_grossissement`, qui simule des scénarios de grossissement ;
  - tester chaque validation métier ;
  - modifier l'API.
- **Anti-objectifs :**
  - une requête jouée avec un rôle que l'écran n'utiliserait pas ;
  - un refus qui écrit quelque chose ;
  - une collection qui exige une base vide ou qui échoue à la deuxième exécution ;
  - une collection maintenue à la main, requête par requête.

## 3. Contraintes

- Prérequis : les quatre comptes de démonstration existent (`admin_demo`, `directeur_demo`, `gestionnaire_demo`, `employe_demo`), avec les identifiants de `pisciculture_local.postman_environment.json`. Les migrations sont appliquées. La cause de mortalité « Inconnue » et la catégorie de perte existent (migrations `effectifs`).
- Références uniques par exécution : `{{fc_run}}`, avec horodatage et suffixe aléatoire, ajouté à toutes les références (bassins, hapas, lots, codes). Deux exécutions donnent deux jeux de données indépendants.
- Dates **fixes dans le passé** (année 2026), pour obtenir un vrai cycle. Seules les clôtures sont datées du jour, parce que l'API l'impose.
- **Tous les lots mis en grossissement passent par un traitement** (décision du 2026-11-17). Pour une chronologie cohérente, l'API évolue d'abord (tâche 0 du plan) :
  - la **clôture d'un traitement accepte une date** facultative (aujourd'hui par défaut), qui ne peut être ni future, ni antérieure à l'ouverture, ni précéder un mouvement des hapas du traitement ;
  - l'aperçu de clôture accepte la même date ;
  - une **mise en stock ne peut pas être antérieure à la clôture de son traitement**.
- Toutes les variables de la collection sont préfixées `fc_`.

## 4. Approches considérées

- **A. Script générateur (retenue)** : un script Python versionné produit le JSON à partir d'une description des étapes en données (étape, rôle, requête, vérifications). Après un changement d'API, on corrige une ligne et on régénère. La structure est uniforme et le rôle de chaque requête garanti. En contrepartie, il faut lire du Python pour faire évoluer la collection.
- B. Collection écrite à la main : modifiable sans outil, mais environ 150 requêtes à maintenir une par une. C'est ce qui a rendu l'ancienne collection obsolète.
- C. Reprise de la collection E2E : elle exige une base vide et joue presque tout en administrateur, la reconvertir revient à la réécrire.

## 5. Scénario (ce que la base contient à la fin)

| Lot | Parcours | État final |
|---|---|---|
| **P** « parcours complet », TILAPIA | reproduction → ponte → éclosion → lot (traitement requis) → traitement (mortalités, clôture datée de janvier) → mise en stock → sortie vers le grossissement avec P0 → cycle : mortalités, provende, paramètres environnementaux, pesées J30, J60, J75… jusqu'au seuil de 250 g → pesée finale récente → clôture (aujourd'hui) → évacuation | lot de grossissement clôturé et évacué, dans l'historique ; lot d'origine `CLOTUREE` |
| **T1** « en traitement », CARPE | lot (traitement requis) → ouverture du traitement → mortalités sur ses hapas | traitement `EN_COURS` |
| **T2** « traité puis stocké », CARPE | lot (traitement requis) → traitement → mortalités → aperçu de clôture → clôture (aujourd'hui) → mise en stock (aujourd'hui) | lot `EN_STOCK` (finalité grossissement), qui montre le passage du traitement au stock |
| **G1 + G2** « en grossissement », CARPE et TILAPIA, même bassin | deux lots traités (clôtures datées de juillet), mis en stock puis sortis vers le même bassin de grossissement → cycle commun : provende par bassin, mortalités par espèce, paramètres environnementaux, pesées J30, J60, J75 | deux lots `ACTIF`, avec suivi, courbes, indicateurs (répartition de la provende entre les deux lots) et une alerte de pesée en retard |

Dates du lot P : il démarre en janvier 2026, entre en grossissement en février, puis reçoit des pesées tous les 15 jours après J60 jusqu'à atteindre le seuil, et une pesée finale récente avant la clôture du jour. Dates de G1 et G2 : entrée en août 2026, pesées jusqu'à J75 ; à la date du jour, l'échéance suivante est dépassée, ce qui montre l'alerte de retard (`PESEE_EN_RETARD`), voulue pour la démonstration. Les pesées récentes de G1 et G2, qui font disparaître l'alerte, ne sont **pas** dans la collection : elles font partie du scénario de test manuel après l'intégration du frontend. Provende : une distribution par semaine et par bassin (le maximum de 3 par jour n'est jamais atteint). Les mortalités sont réparties sur le cycle.

## 6. Rôles et refus

Chaque requête porte le jeton du rôle qui fait l'action dans l'exploitation :
- **Directeur** : référentiels (bassins, hapas, provende), création de la chaîne, ouverture et clôture du traitement, mise en stock, sortie vers le grossissement, clôture, évacuation, corrections ;
- **Employé** : mortalités et pertes, distributions de provende, paramètres environnementaux, pesées ;
- **Gestionnaire** : lectures (listes, suivi, historique).

À côté de chaque étape, un ou deux refus sans écriture (403 attendu) :
- la gérante ou l'employé crée un lot, ouvre ou clôture un traitement, met en stock, valide une sortie, clôture un lot de grossissement ;
- l'employé annule une mortalité ou corrige une pesée ;
- la gérante saisit une mortalité, une distribution ou une pesée ;
- la gérante s'attribue le rôle Administrateur ;
- un accès sans jeton (401).

Une **correction par le directeur** est aussi jouée : l'annulation d'une mortalité saisie par erreur, puis sa ressaisie.

## 7. Vérifications

Chaque requête vérifie le code HTTP et les champs dont le frontend a besoin : identifiants (stockés en variables `fc_`), stade du lot, statut, et pour la sortie le poids moyen initial et l'effectif. Le suivi est vérifié sur les clés du contrat (`docs/contrats/`), les valeurs non nulles attendues et l'alerte de retard. L'historique contient le lot P avec `evacue = true`. Les valeurs exactes des indicateurs ne sont **pas** vérifiées : les tests Django et le dossier 07 de l'ancienne collection s'en chargeaient, et la collection doit rester robuste aux réglages (seuils, arrondis).

## 8. Livrables

- `tests/postman/generer_flux_complet.py` : le générateur, sans dépendance hors bibliothèque standard.
- `tests/postman/flux_complet.postman_collection.json` : le fichier généré, versionné.
- `tests/postman/archives/grossissement_sortie_lot.postman_collection.json` : l'ancienne collection, déplacée et non supprimée. La collection E2E reste en place.
- `tests/postman/README_TEST.md` : prérequis, commandes newman et commande de régénération. Mise à jour de `CLAUDE.md` et de `context/README.md`.
- Vérification : la syntaxe JS de tous les scripts est contrôlée. Le développeur exécute la collection deux fois de suite sur la même base, et les deux exécutions doivent passer.

## 9. Choix laissés à l'implémentation

Les dates exactes dans les fenêtres de la section 5, les poids (croissance plausible jusqu'au seuil), le nombre exact de mortalités et de distributions, et l'ordre des dossiers à l'intérieur de chaque lot.
