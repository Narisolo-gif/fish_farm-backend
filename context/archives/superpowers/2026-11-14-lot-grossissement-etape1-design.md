# Grossissement — étape 1 : mise à niveau du lot de grossissement

Référence : `CONTEXTE_INDICATEURS_GROSSISSEMENT (1).md`, sections 2, 3 et 7.

## Intention
Préparer `LotGrossissement` aux étapes suivantes (pesées, indicateurs, répartition de provende) : règles de création conformes (au plus deux lots d'espèces différentes par bassin, sans hapa) et clôture complète (poids final, effectif final, évacuation dans le registre).

## Décisions
- Les noms du code existant priment sur ceux du document (`ACTIF`, `date_mise_en_place`, `poids_initial_g`, `motif_cloture`, `observation`). Seuls les champs absents sont ajoutés.
- Champs ajoutés : `effectif_final` (entier, nullable), `poids_final_g` (décimal, nullable, > 0). `observation_cloture` n'est pas ajouté (`motif_cloture` existe).
- Poids initial : reste nullable à la création, saisi ensuite par PATCH tant que le lot est ACTIF. La première pesée (étape 2) l'exigera.
- Création (receiver `sortie_stock_validee`, sous verrou du bassin) : bassin sans hapa actif, au plus deux lots ACTIF, espèce différente du lot ACTIF déjà présent. Erreurs sur `id_bassin_destination` ; toute la validation de sortie est annulée.
- Le couple bassin + espèce identifie un lot ACTIF : l'effectif se lit avec `effectif_vivant(bassin, espece, date)` sans modifier `effectifs`.
- Clôture : `motif` et `poids_final_g` obligatoires ; `date_cloture` facultative (défaut aujourd'hui, ≥ date de mise en place, ≤ aujourd'hui). `effectif_final` = effectif vivant à `date_cloture`. Un mouvement `SORTIE` de cet effectif (si > 0) est écrit via `enregistrer_mouvement_operation` (opération = lot de grossissement). `lot_origine.stade` passe à `CLOTUREE`.
- Permissions inchangées : lecture pour tous les rôles actifs, modification et clôture pour Administrateur et Gestionnaire.

## Approches écartées
- Transmettre le poids initial avec la validation de sortie : imposerait de modifier l'API de `effectifs` pour une donnée propre au grossissement.
- Ajouter une FK vers `LotGrossissement` dans le registre : inutile tant que bassin + espèce identifie le lot.

## Hors périmètre
Pesées, indicateurs, répartition de provende, tableau de bord, alertes (étapes 2 à 5). Contrôle « date de clôture ≥ dernière pesée » ajouté à l'étape 2.

## Tests
Tests Django pour chaque règle de création et de clôture ; dossier Postman « Lot de grossissement ».
