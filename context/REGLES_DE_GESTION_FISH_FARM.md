# Fish Farm : règles de gestion implémentées

Référence métier de la plateforme de gestion piscicole de Fish Farm Anosy, **alignée sur le code** (branche `fix/refont-sortie-grossissement`, novembre 2026). Les règles marquées **[À CONFIRMER]** ne sont pas tranchées : ne pas les implémenter sans demander.

Documents liés (voir `context/README.md`) :
- `CONTEXTE_INDICATEURS_GROSSISSEMENT.md` : sortie vers le grossissement, pesées, indicateurs.
- `CONTEXTE_CLOTURE_LOT.md` : clôture d'un lot de grossissement, évacuation, historique.
- `CONTEXTE_PHASE4_PAGE_SUIVI.md` : page de suivi de cycle et son contrat JSON.
- `GUIDE_INTEGRATION_FRONTEND.md` : routes, droits, formats et parcours écran par écran.

## 0. Consignes pour l'assistant de code

- Langue : français (messages d'erreur, libellés, commentaires). Noms de code existants conservés (`id_lot`, `id_bassin`, `effectif_entrant`, `code`, `ACTIF`…).
- Ne rien implémenter hors du périmètre demandé. En cas d'ambiguïté métier, poser la question avant de coder.
- Ne pas supprimer `supprimer_traitement` ni `supprimer_mise_en_stock` : outils de nettoyage en phase de test (la suppression d'une mise en stock est rendue inaccessible côté frontend).
- Tests : l'assistant écrit et lance les tests Django (base `test_` séparée) et les tests unitaires sans base. Il **écrit** les collections Postman ; le développeur les **exécute** avec newman.
- Migrations : l'assistant génère les fichiers (`makemigrations --check` sans écart) et peut les essayer sur une copie jetable de la base ; il **n'applique pas** `migrate` sur la base de développement. Il ne commite pas.
- Les règles ci-dessous prévalent sur toute version antérieure (voir `context/archives/` pour l'historique).

## 1. Contexte technique et conventions

- Backend Django 5.2 + Django REST Framework + SimpleJWT ; frontend Next.js (`http://localhost:3000`) ; PostgreSQL 16 ; Docker Compose.
- Chaîne métier : reproduction → ponte → éclosion → lot → traitement → mise en stock → sortie de stock → grossissement → clôture.
- Logique métier dans `services.py` (`@transaction.atomic`, verrous `select_for_update()` sur bassin, lot, hapa) ; validations aussi dans les serializers ; viewsets minces.
- Erreurs : `ValidationError({"champ": "message"})`, clés alignées sur les champs de l'API (`id_lot`, `id_bassin`, `id_hapa`, `effectif_entrant`, `justification_ecart`, `date_…`, `detail`).
- L'utilisateur est toujours fourni par le serveur (`request.user`). Les dates d'opération sont posées par le serveur, sauf les dates de saisie métier prévues (mise en stock, pesée, mise en bassin, événements, distributions).
- Fuseau du projet : `TIME_ZONE = "Indian/Antananarivo"`. Les dates-heures sont stockées en UTC ; « aujourd'hui » et le jour d'une distribution sont calculés dans ce fuseau.
- Rôles (lus via `user.role.libelle`, utilisateur au `statut` `ACTIF`) : `Administrateur`, `Gestionnaire`, `Employé`. Les droits par route sont dans `GUIDE_INTEGRATION_FRONTEND.md`.
- Rien n'est supprimé physiquement pour les données métier : on annule (`statut`, motif obligatoire, `annulee_par`, `annulee_le`). Exceptions : les deux outils de nettoyage de la section 0.

## 2. Registre d'effectif (app `effectifs`)

### Principe
- `MouvementEffectif` est en **ajout seul** : `save()` sur un mouvement existant et `delete()` lèvent une `ValidationError`.
- Types : `ENTREE`, `SORTIE`, `MORTALITE`, `PERTE`, `AJUSTEMENT_POSITIF`, `VENTE`, `ANNULATION`.
- Effectif = Σ(sens × quantité), **jamais stocké**, toujours calculé (`effectif_vivant`, `effectifs_du_bassin`, `effectifs_par_hapa`). Suivi par bassin + espèce et, quand un hapa existe, par hapa.
- L'effectif n'est **jamais négatif, à aucune date** (contrôle cumulatif à chaque écriture).
- Correction : uniquement par un mouvement `ANNULATION` lié (`annuler_mouvement`) ; un mouvement n'est annulé qu'une fois ; on n'annule pas une annulation.
- Chaque mouvement est daté (`DateField`) et rattaché à son opération d'origine (lien générique `operation_source`).

### Règle de dépendance (stricte)
- Les apps métier importent `effectifs` ; `effectifs` n'importe **aucune** app métier. Il notifie par signaux Django (`send`, pas `send_robust`, dans la même transaction) :
  - `sortie_stock_avant_annulation` : avant l'annulation d'une sortie de stock ;
  - `mouvement_avant_annulation` : avant un contre-mouvement ;
  - `mouvement_avant_enregistrement` : avant tout nouveau mouvement (registre figé d'un lot de grossissement clôturé).
- Même principe pour `provende`, qui émet `distribution_avant_enregistrement` et `distribution_avant_annulation`.

## 3. Hapas

- Enceinte en filet dans un bassin, qui sépare un groupe de poissons.
- **Obligatoire** dans les bassins de TRAITEMENT et de STOCKS ; **interdit** en GROSSISSEMENT (un bassin de grossissement doit être sans hapa actif).
- Un hapa est **neutre** : la finalité (`GROSSISSEMENT` ou `VENTE`) est portée par `MiseEnStock.finalite`.
- Un lot destiné au grossissement ne partage pas son hapa de stock avec un autre lot (le mélange reste possible pour la finalité `VENTE`, ce qui rend alors la provende du hapa non attribuable à un seul lot).

## 4. Traitement (app `traitement`)

- Un lot a au plus un traitement (OneToOne). Ouverture possible pour un lot au stade `CREEE` dont le parcours n'est pas `SANS_TRAITEMENT`.
- Bassin actif de catégorie TRAITEMENT ; **un ou plusieurs hapas** (`TraitementHapa`, chacun avec son effectif initial), sans doublon, du même bassin, libres. `effectif_initial` = somme des hapas, > 0.
- À l'ouverture : statut `EN_COURS`, une `ENTREE` par hapa, lot au stade `EN_TRAITEMENT`.
- Mortalités et pertes du traitement saisies **par hapa** (journal `effectifs`).
- **Clôture** (Administrateur, Gestionnaire) :
  1. Boîte de validation en lecture seule (`apercu-cloture`) : lot, espèce, bassin, dates (ouverture, clôture = aujourd'hui, durée), effectif initial, effectif final, morts et pertes, taux de survie, `cloturable` / `raison_refus`.
  2. Validation : l'`effectif_final` de chaque hapa est **calculé depuis le registre** (jamais saisi ; refusé s'il dépasse l'effectif initial) ; `date_cloture` = aujourd'hui ; statut `CLOTUREE` ; un `BlocageSaisie` est posé sur chaque hapa.
  3. Le frontend propose ensuite la mise en stock (redirection vers le formulaire) ou non. Sans mise en stock, le lot reste `EN_TRAITEMENT` et les hapas restent bloqués.
- Après la clôture : **aucune mortalité ni perte** sur les hapas du traitement tant que la mise en stock n'est pas faite ; la provende reste permise. Un écart de comptage se justifie à la mise en stock (section 5), jamais par une perte.
- L'Employé consulte, ouvre un traitement et lit l'aperçu de clôture ; il ne le clôture pas.

## 5. Mise en stock (app `stocks`)

- Un lot a au plus une mise en stock (OneToOne) ; le lot entier est transféré.
- Conditions : lot au stade `CREEE` (sans traitement) ou `EN_TRAITEMENT` avec traitement `CLOTUREE` (le parcours n'est pas contrôlé : un lot `TRAITEMENT_REQUIS` encore `CREEE` peut être mis en stock sans traitement, voir section 12) ; bassin actif de catégorie STOCKS avec au moins un hapa actif ; `finalite` obligatoire ; **un ou plusieurs hapas** de stock (`MiseEnStockHapa`, effectif entrant par hapa).
- `effectif_entrant` (somme des hapas) > 0 et **jamais supérieur** à la référence : `effectif_final` du traitement, ou `quantite_initiale` du lot sans traitement. `ecart_effectif` = entrant − référence ; si non nul, `justification_ecart` obligatoire ; aucun mouvement de perte pour l'écart.
- `date_mise_en_stock` : saisissable, pas dans le futur.
- Dans une transaction : `SORTIE` de chaque hapa de traitement (refusée si son effectif a bougé depuis la clôture), levée des blocages, `ENTREE` dans chaque hapa de stock, lot au stade `EN_STOCK`.
- **Modification** : refusée dès que la mise en stock a des mouvements, donc en pratique toujours (laissé tel quel, décision du 2026-11-17).
- **Suppression** : outil de test (annule les mouvements, rétablit le blocage du traitement, supprime l'opération, rétablit le stade) ; inaccessible côté frontend.
- Il n'existe pas de statut « clôturé » du stock : il est vidé par la sortie (section 8).

## 6. Mortalités et pertes (journal transversal, app `effectifs`)

- Saisie par hapa en TRAITEMENT et en STOCKS ; par **bassin + espèce** ailleurs (grossissement).
- **Mortalité** : poissons morts, comptés ; `cause_mortalite` obligatoire (liste fermée contenant « Inconnue »).
- **Perte** : poissons disparus sans cadavre ; `categorie_perte` et `justification` obligatoires.
- Chaque saisie crée un mouvement `MORTALITE` ou `PERTE` ; refusée si l'effectif devient négatif à une date quelconque, si le hapa est bloqué (traitement clôturé) ou si le registre est figé (lot de grossissement clôturé).
- Correction par annulation de l'événement (Administrateur, Gestionnaire). L'Employé saisit et consulte.

## 7. Journal de provende (app `provende`)

- La consommation vit dans `provende.ConsommationProvende`, pas dans `effectifs`.
- `date_distribution` : date-heure normalisée à la minute, pas dans le futur, pas antérieure à `Provende.date_creation` (date de production). Quantité en kg, ≥ 0.
- Cible : hapa obligatoire en TRAITEMENT et en STOCKS ; bassin entier ailleurs. La cible doit contenir au moins un poisson vivant à la date.
- Au plus **3 distributions valides par jour** et par cible, toutes provendes confondues ; doublon (même cible, même provende, même minute) refusé. Mode libre (pas de plan de ration).
- Autorisée après la clôture d'un traitement.
- En **grossissement** : saisie par bassin, jamais par lot (la répartition entre lots est calculée). Refusée si aucun lot `ACTIF` n'est en place à la date, ou si elle est datée de la période d'un lot clôturé (de sa mise en place à sa clôture) ; l'annulation d'une distribution de cette période est aussi refusée (chiffres biologiques figés).
- Correction par annulation (Administrateur, Gestionnaire). L'Employé saisit et consulte.

## 8. Sortie de stock

- Seule la sortie vers le **grossissement** est implémentée (finalité `GROSSISSEMENT`), par le flux unique de la section 9. La sortie pour la finalité `VENTE` n'est **pas** implémentée (décision du 2026-11-17).
- Sortie **totale** du lot d'origine ; les lignes par hapa sont calculées par le serveur. Statuts `VALIDEE` / `ANNULEE` (pas de brouillon).
- Annulation (Administrateur, Gestionnaire, motif obligatoire) : contre-mouvements, lot d'origine revenu `EN_STOCK`, lot de grossissement supprimé ; refusée si le lot de grossissement a une pesée (même annulée) ou est clôturé.

## 9. Grossissement (résumé)

Détail : `CONTEXTE_INDICATEURS_GROSSISSEMENT.md` et `CONTEXTE_CLOTURE_LOT.md`.

- **Sortie et création du lot en une transaction** (Administrateur, Gestionnaire) : aperçu sans écriture, puis validation. Pesée initiale P0 sur échantillon (10 % par défaut) ; `effectif_entrant` ≤ quantité sortie (écart en moins justifié, écart en plus refusé) ; code proposé (`GR-AAAA-NNNN`) modifiable et unique ; bassin GROSSISSEMENT actif, sans hapa, au plus **deux lots `ACTIF`** d'espèces différentes.
- **Pesées** de suivi par lot, sur échantillon ; indicateurs (poids moyen, biomasse, GMQ, ICA) par lot.
- **Seuil de vente par espèce**, jugé sur la **dernière** pesée valide : TILAPIA 250 g, CARPE 300 g **[À CONFIRMER par l'exploitant]**. Simple signal ; précondition de la clôture ; jamais de clôture automatique.
- **Clôture** à la date du jour, sans saisie, puis **évacuation** différable du bassin.
- Statuts du lot de grossissement : `ACTIF`, `CLOTURE`. Stade du lot d'origine : `EN_GROSSISSEMENT`, puis `CLOTUREE` à la clôture.

## 10. Alertes

- Suivi de cycle : `PESEE_EN_RETARD` (tolérance `TOLERANCE_RETARD_PESEE_JOURS = 0`) et `POIDS_VENTE_ATTEINT` (seuil de l'espèce). Calculées à la volée, jamais stockées, seulement pour un lot `ACTIF`.
- Plus tard, seuils paramétrables : mortalité anormale, écart d'effectif entre deux étapes.

## 11. Décisions : ne pas implémenter sans nouvelle demande

- Modification d'une mise en stock (aujourd'hui impossible en pratique).
- Sortie de stock à finalité `VENTE`.
- Suppression d'une mise en stock depuis le frontend.

## 12. Points ouverts

- Seuil de vente de la carpe (300 g).
- Biais de l'ICA calculé sur les poissons vivants seulement : accepté ou non.
- Âge maximal de la dernière pesée pour clôturer un lot de grossissement.
- Unités des paramètres environnementaux (°C, mg/L…), non enregistrées en base. (Droits tranchés : les trois rôles lisent, saisissent et modifient ; seul l'Administrateur supprime une mesure.)
- Attribution de la provende d'un hapa de stock partagé par plusieurs lots (finalité `VENTE`).
- Mise en stock d'un lot `TRAITEMENT_REQUIS` sans traitement : aujourd'hui acceptée (aucun contrôle du parcours).
- Fin de vie d'un lot perdu avant le grossissement (effectif nul en traitement ou en stock) : à concevoir. La route `PATCH /api/v1/lots/{id}/cloturer/`, qui forçait `CLOTUREE` sans contrôle, est bloquée (commentée dans `apps/lots/viewsets.py`).

## 13. Décisions écartées (ne pas les réintroduire)

- Finalité portée par le hapa.
- Mortalité ou perte permise après la clôture d'un traitement ; effectif courant du hapa utilisé pour la mise en stock.
- Effectif stocké en colonne ou modifiable.
- Dépendance de `effectifs` ou de `provende` vers les apps de production.
- `send_robust` pour les signaux.
- Consommation de provende dans `effectifs`.
- Brouillon et validation en deux temps pour la sortie vers le grossissement ; création du lot de grossissement par signal.
- Poids initial saisi seul (`poids_initial_g`) : remplacé par la pesée initiale d'échantillon.
- Écart en plus à la sortie vers le grossissement.
- Pesée de bassin couvrant plusieurs lots (`PeseeLot`), pesée par recomptage complet ou par classes de taille (`PeseeGroupe`).
- Suppression physique d'une pesée (remplacée par l'annulation).
- Sortie partielle d'un stock vers le grossissement, répartition entre plusieurs lots.
- Interpolation de la biomasse ou de l'ICA entre deux pesées.
- Clôture automatique au seuil ; poids final ou motif saisis à la clôture ; évacuation automatique à la clôture.
