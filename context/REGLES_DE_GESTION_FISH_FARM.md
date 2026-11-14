# Fish Farm : règles de gestion retenues (contexte pour le développement)

Ce document décrit les règles métier arrêtées pour la plateforme de gestion piscicole de Fish Farm Anosy. Il sert de référence à un assistant de code. Les règles marquées **[À CONFIRMER]** n'ont pas été tranchées : ne pas les implémenter sans demander. Le suivi de grossissement (pesées, indicateurs, clôture) est détaillé dans `CONTEXTE_INDICATEURS_GROSSISSEMENT.md`, le service IA dans `CONTEXTE_SERVICE_IA.md`.

## 0. Consignes pour l'assistant de code

- Langue : français (messages d'erreur, libellés, commentaires). Noms de code existants conservés (`id_lot`, `id_bassin`, `effectif_entrant`, etc.).
- Ne rien implémenter hors du périmètre demandé. En cas d'ambiguïté métier, poser la question avant de coder.
- Ne pas supprimer `supprimer_traitement` : il sert au nettoyage en phase de test.
- Les tests Postman sont **écrits** par l'assistant et **exécutés par le développeur**. L'assistant ne lance que les tests unitaires sans base de données. Il n'applique pas les migrations (il génère seulement les fichiers) et ne commite pas.
- Les règles ci-dessous prévalent sur toute version antérieure du code.

## 1. Contexte technique

- Backend Django + Django REST Framework ; frontend Next.js ; PostgreSQL ; Docker.
- Service IA séparé (FastAPI + LangGraph, détection d'anomalies déterministe, LLM pour l'explication seulement). Hors périmètre de ce document ; voir `CONTEXTE_SERVICE_IA.md`.
- Apps Django : `bassins`, `lots`, `traitement`, `stocks`, `provende`, `effectifs`, `grossissement`.
- Chaîne métier : reproduction → ponte → éclosion → lots → traitement → stock → grossissement.

### Conventions de code observées
- Logique métier dans `services.py` (fonctions décorées `@transaction.atomic`), validations aussi dans les serializers, viewsets minces.
- Verrouillage `select_for_update()` sur le lot et l'opération concernés avant toute modification.
- Erreurs : `rest_framework.exceptions.ValidationError({"champ": "message"})`, avec les clés `id_lot`, `id_bassin`, `justification_ecart`, `effectif_entrant`, `detail`.
- Serializers : champs `id_lot` / `id_bassin` (`PrimaryKeyRelatedField` avec `source`), `reference_lot`, `reference_bassin`, `id_user` en lecture seule, `ecart_effectif` en lecture seule.
- L'utilisateur est toujours fourni par le serveur (`request.user`), jamais par le client. Les dates d'opération sont posées par le serveur, sauf les dates de saisie métier explicitement prévues (pesée, mise en bassin).
- Permissions : classe `*Permission` par module. Utilisateur actif (`is_active`) et `statut == "ACTIF"`. Rôle lu via `user.role.libelle` : `Administrateur`, `Gestionnaire`, `Employé`.
- Module stocks : Administrateur et Gestionnaire ont tout le CRUD ; l'Employé peut seulement créer (`create`).
- Énumérations existantes : `Bassin.Statut.ACTIF`, `Bassin.Categorie` (dont `STOCKS`, `TRAITEMENT`, `GROSSISSEMENT`, `REPRODUCTION`), `Lot.Stade` (`CREEE`, `EN_TRAITEMENT`, `EN_STOCK`, `EN_GROSSISSEMENT` et stades suivants), `Traitement.Statut` (`EN_COURS`, `CLOTUREE`).

## 2. Registre d'effectif (app `effectifs`)

### Principe
- Modèle `MouvementEffectif` en **ajout seul** : jamais de modification ni de suppression d'une ligne. Les gardes lèvent `RegistreEnAjoutSeulError`.
- Types : `ENTREE`, `SORTIE`, `MORTALITE`, `PERTE`, `AJUSTEMENT_POSITIF`, `VENTE`, `ANNULATION`.
- Effectif = Σ(sens × quantité). **Jamais stocké**, toujours calculé.
- L'effectif ne doit **jamais être négatif, à aucune date** (pas seulement à la date courante).
- Toute correction se fait par un mouvement `ANNULATION` lié au mouvement annulé (`annule_mouvement`, OneToOne : un mouvement ne peut être annulé qu'une fois). On n'annule pas une annulation.
- Chaque mouvement est daté et rattaché à son opération d'origine (traitement, mise en stock, sortie de stock, etc.).
- Quantités entières exactes, strictement positives.
- Fonctions de calcul : `effectif_vivant`, `effectifs_du_bassin` (module `calculs`). Le suivi se fait par bassin et, quand un hapa existe, par hapa.

### Définitions
- **Mortalité** : poissons morts, comptés.
- **Perte** : poissons disparus sans cadavre.
- **Ajustement positif** : ajout d'effectif justifié (correction de comptage à la hausse).
- Cause obligatoire pour mortalité et perte, choisie dans une liste fermée qui contient « inconnue ».

### Règle de dépendance (stricte)
- Les apps métier importent `effectifs` (services `enregistrer_mouvement_operation`, `operation_a_mouvements`, etc.).
- `effectifs` n'importe **aucune** app métier. Il notifie les modules par **signaux Django** : `sortie_stock_validee`, `sortie_stock_avant_annulation`.
- Les signaux sont émis avec `send` (pas `send_robust`), dans la même transaction que l'opération : une erreur dans un récepteur annule tout.
- Exception de flux : la sortie vers le grossissement et la création du lot sont faites par un service unique (section 9), sans passer par le signal.

## 3. Hapas

- Un hapa est une enceinte en filet dans un bassin, qui sépare un groupe de poissons.
- **Obligatoire** dans les bassins de TRAITEMENT et de STOCKS. **Interdit** en REPRODUCTION et en GROSSISSEMENT.
- Un hapa est **neutre** : il ne porte aucune finalité. La finalité (`GROSSISSEMENT` ou `VENTE`) est portée par `MiseEnStock.finalite`, décidée à la mise en stock.
- En pratique : un lot par hapa. Cette règle résout le mélange des lots et rend la consommation de provende attribuable à chaque lot.
- Vérification hapa : fonction `_verifier_hapa` dans les services de traitement et de mise en stock (hapa actif, appartenant au bassin choisi, sans autre lot).

## 4. Traitement (app `traitement`)

- Un lot a au plus un traitement (OneToOne). `effectif_initial` > 0.
- Ouverture : statut `EN_COURS`, bassin actif de catégorie TRAITEMENT, hapa renseigné. Le lot passe au stade `EN_TRAITEMENT`.
- `effectif_final` : saisi **uniquement à la clôture** ; vaut effectif de départ moins les morts ; 0 autorisé ; ≤ `effectif_initial` (contrainte en base).
- `date_cloture` posée par le service de clôture. Le statut est géré par le backend uniquement.
- `used_provende` : provende utilisée (FK vers `provende.Provende`).
- Mortalités du traitement saisies **par hapa**.
- Après clôture : **aucune mortalité ni perte** ne peut être saisie sur les hapas du traitement tant que le transfert n'est pas fait. Mécanisme : `BlocageSaisie`. La provende n'est **pas** bloquée.
- Une erreur de saisie après clôture ne se corrige pas par une perte : l'écart est justifié à l'étape suivante (voir `ecart_effectif`, section 5).
- Liste des traitements triée du plus récent au plus ancien (`-date_ouverture`, `-id`).

## 5. Mise en stock (app `stocks`)

- Un lot a au plus une mise en stock (OneToOne). Le lot entier est transféré.
- Conditions de création :
  - lot au stade `CREEE` ou `EN_TRAITEMENT` ;
  - si le lot a un traitement, il doit être `CLOTUREE` et avoir un `effectif_final` ;
  - bassin actif et de catégorie `STOCKS` ;
  - hapa obligatoire, valide (section 3) ;
  - `effectif_entrant` > 0 et **jamais supérieur** à la référence (`effectif_final` du traitement, ou `quantite_initiale` du lot sans traitement).
- `ecart_effectif = effectif_entrant − effectif_final` (traitement clôturé). Pour un lot sans traitement, la référence est `quantite_initiale`. Si l'écart est non nul, `justification_ecart` est **obligatoire**. Aucun mouvement de perte n'est créé pour l'écart.
- Finalité (`finalite`) saisie ici : `GROSSISSEMENT` ou `VENTE`.
- Le lot passe au stade `EN_STOCK` dans la même transaction. Un mouvement `ENTREE` est enregistré dans le registre.
- Modification : possible seulement tant que le lot est `EN_STOCK` ; le lot ne peut pas être changé ; champs modifiables : bassin, hapa, effectif entrant, justification, observation. Les règles de création (dont l'écart) sont revérifiées.
- Suppression : possible seulement si le lot est encore `EN_STOCK`. Le lot revient à `EN_TRAITEMENT` (s'il a un traitement) ou à `CREEE`.
- `date_mise_en_stock` automatique ; `utilisateur` fourni par le serveur.

## 6. Mortalités et pertes (journal transversal)

- Saisie par hapa dans les bassins de traitement et de stock ; par bassin (ou par lot de grossissement, voir `CONTEXTE_INDICATEURS_GROSSISSEMENT.md`) lorsqu'il n'y a pas de hapa.
- Chaque saisie crée un mouvement `MORTALITE` ou `PERTE` dans le registre ; refus si l'effectif disponible devient négatif.
- Correction par annulation uniquement.
- Blocage après clôture d'un traitement (section 4).

## 7. Journal de provende (app `provende`)

- La consommation de provende vit dans l'app `provende`, **pas** dans `effectifs`.
- `date_distribution` est un `DateTimeField` normalisé à la minute.
- Maximum **3 distributions valides par jour** et par hapa (ou par bassin quand il n'y a pas de hapa), **toutes provendes confondues**.
- Un doublon (même cible, même minute) est refusé.
- Mode libre : l'enregistrement reste possible sans respecter un plan de ration fixe.
- Autorisée après la clôture du traitement.
- En grossissement, la provende est saisie **par bassin**, jamais par lot ; la répartition entre lots est calculée (voir le contexte indicateurs).
- `Provende.date_creation` = date de production de la provende.
- Types de provende : `TypeProvende` existant.

## 8. Sortie de stock (autres finalités)

- Périmètre : **sortie seulement**. La vente est hors périmètre.
- Porte sur le **lot d'origine entier** ; les lignes (quantités) sont calculées par le serveur, pas saisies.
- La finalité est lue via `lot.mise_en_stock.finalite`.
- Refus si le lot partage son hapa avec un autre lot.
- Crée un mouvement `SORTIE` dans le registre et émet `sortie_stock_validee`.
- L'annulation émet `sortie_stock_avant_annulation` avant d'annuler les mouvements.
- La sortie dont la finalité est `GROSSISSEMENT` suit le flux dédié de la section 9.

## 9. Sortie vers le grossissement et lot de grossissement

### 9.1 Droits
- Seuls l'**Administrateur** et le **Gestionnaire** créent et valident la sortie vers le grossissement et créent le lot. L'**Employé** a la lecture seule des lots. Utilisateur inactif ou non connecté : refusé.

### 9.2 Flux utilisateur (aucun brouillon)
1. **La sortie** : choix du stock (sortie **totale** : un stock donne un lot) et du **bassin de destination**.
2. **La pesée initiale P0** : date de la pesée, pourcentage d'échantillon (**10 par défaut**), nombre de poissons pesés, poids total. Le poids moyen est calculé (poids total ÷ nombre pesé), jamais saisi. Les chiffres sont contrôlés.
3. **Le formulaire du lot** :
   - saisis ou modifiables : code de référence, date de mise en bassin, `effectif_entrant`, justification de l'écart ;
   - préremplis et non modifiables : lot d'origine, espèce, bassin, date de pesée initiale, poids moyen initial.
4. **Valider** : un seul appel écrit tout. **Annuler** : rien n'est écrit.

Il n'existe **ni brouillon ni validation en deux temps** pour cette sortie : les écrans gardent leurs données côté frontend jusqu'au clic sur Valider. Un appel d'aperçu en lecture seule permet d'afficher les erreurs et les valeurs préremplies avant la validation, sans rien écrire.

### 9.3 Atomicité
Toute la validation se fait dans **une seule transaction**. Toutes les validations sont faites avant les écritures. En cas d'erreur à n'importe quelle étape, l'état est exactement celui d'avant : aucun mouvement, stock non clôturé, stade du lot d'origine inchangé, aucun lot de grossissement.

À la validation, dans l'ordre : sortie du stock, clôture du stock, mouvements d'effectif, passage du lot d'origine au stade `EN_GROSSISSEMENT`, création du `LotGrossissement` (prêt à être suivi).

### 9.4 Effectif et écart
- Le stock est décrémenté de la **quantité sortie**. Le lot est créé avec `effectif_initial = effectif_entrant`.
- `effectif_entrant` peut différer de la quantité sortie, dans les deux sens ; tout écart exige `justification_ecart`. Aucun mouvement de perte n'est créé pour l'écart (même principe que la section 5).
- Mouvements : `SORTIE` de la quantité sortie du bassin de stock, `ENTREE` de `effectif_entrant` dans le bassin de grossissement, rattachés au lot d'origine.

### 9.5 Pesée initiale (P0)
- Le lot est pesé **avant** sa mise en bassin. P0 est un échantillon qui ne crée **aucune `Pesee`** : ce n'est pas une pesée du calendrier. Ses valeurs sont conservées comme trace sur le lot.
- Taille cible = arrondi supérieur de `pourcentage × quantité sortie / 100`, minimum 1.
- Échantillon inférieur à la cible : refusé tant que le client n'a pas envoyé `confirmer_echantillon_non_conforme: true` (le frontend affiche une confirmation). Même mécanisme que les pesées de suivi.
- Le nombre de poissons pesés ne dépasse ni la quantité sortie ni `effectif_entrant`.
- `poids_moyen_initial` = poids total ÷ nombre pesé, arrondi demi supérieur à 2 décimales, sur le résultat final seulement.

### 9.6 Dates
- `date_pesee_initiale ≤ date_mise_en_bassin ≤ aujourd'hui`. Même jour autorisé, pas de date future. La mise en bassin peut être rétroactive. Le blocage de saisie existant s'applique. `date_entree` du lot = date de mise en bassin.

### 9.7 Bassin et lots
- Bassin actif, catégorie `GROSSISSEMENT`, **sans hapa**.
- Au plus **deux lots `EN_COURS`** par bassin ; si un lot est déjà présent, l'**espèce doit être différente**.
- La référence du lot est proposée par le système (format existant), modifiable, **unique**.
- L'espèce se lit via le lot d'origine, sans duplication.
- Le lot de grossissement est un cycle (un cycle par lot). Pas de fusion de lots.

### 9.8 Seuil et clôture
- Seuil de **250 g** de poids moyen : simple signal « fin de cycle possible », sans action automatique. La clôture est manuelle (voir le contexte indicateurs).

## 10. Pesées et indicateurs (résumé)

Détails, formules et modèle de données : `CONTEXTE_INDICATEURS_GROSSISSEMENT.md`.

- La pesée de suivi est un **échantillon** (10 % par défaut) qui remplace le recomptage complet et ne modifie jamais l'effectif. Il n'y a pas de `PeseeGroupe`.
- Calendrier : J30, J60, puis tous les 15 jours (J75, J90, …), compté depuis la date d'entrée, y compris au-delà de J180. Il sert à la prochaine pesée prévue et à l'alerte de retard ; il ne bloque jamais la saisie.
- Indicateurs par **lot** : poids moyen, biomasse, GMQ, ICA, calculés sur les poissons **vivants**. L'ICA (indice de conversion alimentaire, FCR) est le même indicateur que l'« IC » du code (`ic_periode`, `ic_cumule`) : employer « ICA » dans les libellés visibles.
- Aucun calcul quotidien ni interpolation. Après la dernière pesée, la biomasse reste celle de cette pesée, datée. Valeurs **mesurées** (poids moyen) distinguées des **estimées** (biomasse).
- Correction ou suppression d'une pesée : seulement la dernière du lot, lot `EN_COURS`.

## 11. Alertes

- Retenues pour le suivi de cycle : **pesée en retard** (avec une tolérance en jours, valeur par défaut 0 **[À CONFIRMER]**) et **poids de vente atteint** (250 g). Calculées à la volée, jamais stockées.
- Prévues plus tard, seuils paramétrables : mortalité anormale, écart d'effectif entre deux étapes.

## 12. Ordre de réalisation et reste à faire

1. Sortie vers le grossissement et création du lot en une transaction (section 9).
2. Pesée de suivi (phase 1), indicateurs (phase 2), données simulées (phase 3), suivi de cycle (phase 4).
3. Clôture du lot, service IA, intégration Django de l'analyse.
4. Frontend du suivi (formulaires de sortie et de pesée, tableau de bord, courbes).
- Adaptation des tests Postman aux règles récentes (hapa, finalité à la mise en stock, blocage après clôture, nouveau flux de sortie).
- Modèle d'import Excel : retirer la colonne `finalite` de la feuille Hapas.
- Commandes et ventes : perspective, hors périmètre actuel.

## 13. Points ouverts

- Tolérance de retard de pesée (0 jour par défaut).
- Biais de l'ICA calculé sur les poissons vivants seulement : accepté ou non.
- Mouvement `SORTIE` à la clôture du lot et valeur par défaut de `poids_moyen_final`.
- Unité du taux d'oxygène et liste complète des paramètres environnementaux ; emplacement du modèle `ParametreEnvironnemental`.
- Représentation exacte de la « clôture » du stock à la sortie (à relever dans le code).

## 14. Décisions écartées (ne pas les réintroduire)

- Finalité portée par le hapa.
- Mortalité ou perte permise après clôture du traitement, ou utilisation de l'effectif courant du hapa pour la mise en stock.
- Effectif stocké en colonne ou modifiable.
- Dépendance de `effectifs` vers les apps métier.
- `send_robust` pour les signaux de sortie de stock.
- Consommation de provende dans `effectifs`.
- Brouillon et validation en deux temps pour la sortie vers le grossissement.
- Poids initial saisi seul (`poids_initial_g`) : remplacé par la pesée initiale d'échantillon.
- Création du lot de grossissement par signal.
- Pesée par recomptage complet, par groupes ou par classes de taille (`PeseeGroupe`).
- Sortie partielle d'un stock vers le grossissement et répartition entre plusieurs lots.
- Interpolation de la biomasse ou de l'ICA entre deux pesées.
- Clôture automatique à 250 g.
