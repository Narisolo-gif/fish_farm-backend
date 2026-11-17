# Fish Farm : grossissement, pesées et indicateurs (implémenté)

Complète `REGLES_DE_GESTION_FISH_FARM.md` (section 9). Décrit le modèle de données, la sortie vers le grossissement, les pesées, les formules des indicateurs et la répartition de la provende, **tels qu'implémentés** dans l'app `grossissement`. La clôture et l'évacuation sont dans `CONTEXTE_CLOTURE_LOT.md`, la page de suivi dans `CONTEXTE_PHASE4_PAGE_SUIVI.md`, les routes dans `GUIDE_INTEGRATION_FRONTEND.md`.

Vocabulaire : « ICA » (indice de conversion alimentaire, FCR) dans les libellés visibles ; le code garde `ic_periode` et `ic_cumule`.

## 1. Principes

- Indicateurs (poids moyen, GMQ, ICA, biomasse) calculés **par lot**. Deux lots d'espèces différentes peuvent partager un bassin ; chacun a ses indicateurs.
- Provende enregistrée **par bassin**, répartie entre les lots au prorata de leur biomasse vivante (section 6).
- Pesée sur **échantillon** : elle ne modifie jamais l'effectif, qui vient toujours du registre `effectifs`.
- Paramètres biologiques sur les **poissons vivants** uniquement.
- Le lot est **pesé avant sa mise en bassin** (P0) : son poids moyen est le point de départ des indicateurs.
- Hapas **interdits** en grossissement. Un lot de grossissement est identifié dans le registre par **bassin + espèce** (d'où au plus un lot `ACTIF` par espèce et par bassin).

## 2. Modèle de données (app `grossissement`)

### LotGrossissement
| Champ | Règle |
|---|---|
| `code` | unique ; proposé `GR-AAAA-NNNN`, modifiable à la création (API : `code`) |
| `lot_origine` | FK `lots.Lot` ; l'espèce est copiée dans `espece` à la création |
| `sortie_stock` | OneToOne `effectifs.SortieStock` (la sortie qui a créé le lot) |
| `bassin` | FK `bassins.Bassin`, catégorie GROSSISSEMENT |
| `date_mise_en_place` | date-heure de mise en bassin (API : `date_entree` = sa date locale) |
| `effectif_initial` | > 0, égal à `effectif_entrant` |
| `poids_moyen_initial` | g, calculé depuis P0 |
| `date_pesee_initiale`, `pourcentage_echantillon_initial`, `nombre_poissons_peses_initial`, `poids_total_initial_g` | trace de P0, lecture seule |
| `pourcentage_groupe_pesee` | % d'échantillon par défaut des pesées de suivi (10), modifiable tant que le lot est `ACTIF` |
| `statut` | `ACTIF` ou `CLOTURE` |
| `date_cloture`, `effectif_final`, `poids_moyen_final`, `motif_cloture` | renseignés à la clôture (`motif_cloture` = observation facultative) |
| `evacue_le`, `evacue_par` | évacuation du bassin après la clôture |
| `observation` | modifiable tant que le lot est `ACTIF` |

### Pesee (une pesée = un lot, une date)
`lot_grossissement`, `bassin` (copié du lot), `date`, `pourcentage_echantillon` (cible ; défaut = `pourcentage_groupe_pesee` du lot), `taille_echantillon_cible`, `nombre_poissons_peses`, `poids_total_g`, `poids_moyen` (calculé), `pourcentage_reel` (calculé), `effectif_vivant` (trace de la saisie), `observation`, `statut` (`VALIDEE` / `ANNULEE`), `motif_annulation`, `annulee_par`, `annulee_le`, `utilisateur`, `created_at`, `updated_at`. Contrainte : une seule pesée `VALIDEE` par lot et par date.

### IndicateurLot (cache dérivé, OneToOne `Pesee`)
`effectif_vivant` (relu dans le registre), `poids_moyen`, `biomasse_kg`, `gmq_periode`, `gmq_cumule`, `provende_periode_kg`, `ic_periode`, `ic_cumule`, `jour_de_cycle`. Valeurs non calculables à `null` (sauf `provende_periode_kg`, 0 s'il n'y a rien). Supprimé quand sa pesée est annulée.

### ParametreEnvironnemental (app `environnement`, par bassin)
`date_heure_prelevement`, `temperature_eau`, `temperature_air`, `oxygene`, `ph`, `nitrite`, `nitrate`, `ammoniac` (unités non enregistrées).

## 3. Sortie de stock et création du lot (une transaction)

- **Droits** : Administrateur et Gestionnaire ; Employé en lecture.
- **Aperçu** `POST lots-grossissement/apercu-sortie/` : mêmes contrôles, valeurs préremplies et calculées (lignes par hapa, quantité sortie, code proposé, taille cible, poids moyen initial, conformité, écart), rien n'est écrit.
- **Validation** `POST lots-grossissement/` : sortie totale (une `SORTIE` par hapa de stock), `ENTREE` de `effectif_entrant` dans le bassin de grossissement, `SortieStock` `VALIDEE`, lot d'origine `EN_GROSSISSEMENT`, création du `LotGrossissement`. Une erreur ne laisse aucune trace.
- **P0** : `date_pesee_initiale`, `pourcentage_echantillon` (10 par défaut), `nombre_poissons_peses`, `poids_total_g`. Cible = arrondi supérieur de `pourcentage × quantité sortie / 100`, minimum 1. Sous la cible : refusé sans `confirmer_echantillon_non_conforme: true`. Nombre pesé ≤ quantité sortie et ≤ `effectif_entrant`. Aucune `Pesee` créée.
- **Lot** : `code` (vide = code proposé), `date_mise_en_place`, `effectif_entrant` (≤ quantité sortie ; écart en moins → `justification_ecart` obligatoire ; écart en plus refusé), `observation`. Tout autre champ (dont `poids_initial_g`) est refusé.
- **Dates** : `date_pesee_initiale ≤ date_mise_en_place ≤ aujourd'hui`.
- **Bassin** (vérifié sous verrou) : actif, GROSSISSEMENT, sans hapa actif ; au plus deux lots `ACTIF` ; espèce différente du lot présent ; pas de lot clôturé non évacué de la même espèce ; pas de mouvement de la même espèce postérieur à la mise en place.

## 4. Mortalité et perte en grossissement

- Saisie par **bassin + espèce** dans le journal `effectifs` (`evenements-mortalite-perte`), sans hapa : le lot est celui de l'espèce dans le bassin.
- Mortalité : cause obligatoire ; perte : catégorie et justification obligatoires. Refus si l'effectif devient négatif ; correction par annulation.
- Registre figé après la clôture (voir `CONTEXTE_CLOTURE_LOT.md`).

## 5. Journal de provende

- Journal de l'app `provende` (`ConsommationProvende`, `quantite_kg`), par bassin, horodaté à la minute, au plus 3 distributions valides par jour, doublon refusé.
- En grossissement : refusée si aucun lot `ACTIF` n'est en place à la date, ou si elle est datée de la période d'un lot clôturé ; l'annulation d'une distribution de cette période est refusée.
- Aucune saisie par lot : la répartition est calculée (section 6).

## 6. Pesées et indicateurs

### Saisie d'une pesée (`pesees/`)
- Saisis : `id_lot_grossissement`, `date`, `nombre_poissons_peses`, `poids_total_g` ; facultatifs : `pourcentage_echantillon`, `observation`, `confirmer_echantillon_non_conforme`. Tout autre champ est refusé.
- Déduits : espèce, bassin, effectif vivant (registre à la date), taille cible, poids moyen, pourcentage réel, utilisateur.
- Aperçu `POST pesees/apercu/` : formulaire prérempli (effectif vivant, cible, dernière pesée, date minimale ; poids moyen et pourcentage réel si les mesures sont fournies), rien n'est écrit.
- Validations : lot `ACTIF`, bassin actif ; `date` ≤ aujourd'hui, ≥ mise en place, **strictement postérieure à la dernière pesée valide** ; effectif vivant > 0 ; 0 < nombre pesé ≤ effectif vivant ; poids total > 0 ; sous la cible → confirmation obligatoire.
- La date est libre dans le cycle : le calendrier n'impose rien.
- **Correction** (`PATCH`, Administrateur et Gestionnaire) : seulement la dernière pesée valide d'un lot `ACTIF` ; nombre, poids, pourcentage, observation ; la date et le lot sont figés.
- **Annulation** (`POST pesees/{id}/annuler/`, motif obligatoire, Administrateur et Gestionnaire) : seulement la dernière pesée valide ; la précédente redevient corrigeable ; la pesée reste consultable. Pas de suppression (`DELETE` → 405).
- Une pesée, même annulée, empêche d'annuler la sortie de stock du lot.

### Calendrier (indicatif, jamais bloquant)
- Échéances J30, J60, puis tous les 15 jours (J75, J90, …), sans limite, comptées depuis la date d'entrée.
- **Prochaine pesée** = première échéance strictement postérieure à la dernière pesée valide (J30 s'il n'y en a pas).
- **Retard** : date de référence > date prévue + `TOLERANCE_RETARD_PESEE_JOURS` (0).

### Formules (pour un lot, à la pesée n)
Calculs en `Decimal`, arrondi demi supérieur appliqué **uniquement au résultat final**.
- `poids_moyen_n` = `poids_total_g` / `nombre_poissons_peses` (g, 2 décimales).
- `effectif_vivant_n` = effectif du lot à la date de la pesée, **relu dans le registre à chaque recalcul** (une mortalité saisie après coup est donc prise en compte au recalcul suivant).
- `biomasse_n` (kg, 3 décimales) = `effectif_vivant_n` × `poids_moyen_n` / 1000.
- Point de départ : `poids_moyen_0` = `poids_moyen_initial` ; `biomasse_0` = `effectif_initial` × `poids_moyen_0` / 1000.
- `gmq_periode_n` (g/j, 2 décimales) = (`poids_moyen_n` − `poids_moyen_n-1`) / jours entre les deux pesées ; `null` si 0 jour.
- `gmq_cumule_n` = (`poids_moyen_n` − `poids_moyen_0`) / jours depuis la date d'entrée ; `null` si 0 jour.
- `provende_periode_n` (kg, 3 décimales) = somme de la provende **allouée au lot** sur la période.
- `ic_periode_n` (ICA, 2 décimales) = `provende_periode_n` / (`biomasse_n` − `biomasse_n-1`) ; `null` si le gain est ≤ 0 ou si la provende est 0.
- `ic_cumule_n` = provende cumulée allouée / (`biomasse_n` − `biomasse_0`), même règle.
- ICA et biomasse sur poissons vivants : un lot à forte mortalité a un ICA plus élevé **[À CONFIRMER si ce biais est accepté]**.
- Aucune interpolation entre deux pesées : les dernières valeurs sont affichées avec la date de la pesée.

### Période de provende
Une distribution compte dans la période n si sa **date locale** est `>` la date de la pesée n−1 et `≤` la date de la pesée n. Première période : borne basse `≥` date d'entrée.

### Répartition de la provende entre les lots d'un bassin
Jour par jour (distributions valides regroupées par date locale D) :
1. Lots présents : date d'entrée ≤ D et non clôturés avant D.
2. Biomasse de référence = effectif vivant à D × dernier poids moyen connu **strictement avant** D (sinon `poids_moyen_initial`).
3. Répartition au prorata (la somme des parts est exactement la quantité ; le reste d'arrondi va au lot de plus grande biomasse).
4. Aucun lot présent ou biomasse nulle : rien n'est alloué.

Chaque création, correction ou annulation de pesée recalcule les instantanés de **tous les lots du bassin**, sur les pesées valides, dans la même transaction.

## 7. Suivi, seuil et clôture

- **Seuil de vente** par espèce (`SEUIL_POIDS_VENTE_G`) : TILAPIA 250 g, CARPE 300 g **[À CONFIRMER pour la carpe]**, jugé sur la **dernière** pesée valide : signal « fin de cycle possible » et précondition de clôture.
- **Suivi de cycle** `GET lots-grossissement/{id}/suivi/` : voir `CONTEXTE_PHASE4_PAGE_SUIVI.md` et `docs/contrats/`.
- **Clôture, évacuation, historique** : voir `CONTEXTE_CLOTURE_LOT.md`.
- Constantes métier, seules valeurs à modifier : `apps/grossissement/calculs.py` (`PREMIERES_ECHEANCES_JOURS`, `INTERVALLE_PESEE_JOURS`, `TOLERANCE_RETARD_PESEE_JOURS`, `SEUIL_POIDS_VENTE_G`).

## 8. Données simulées

`python manage.py seed_grossissement [--dry-run] [--scenarios …] [--seed N]` : écrit des scénarios complets (sortie, mortalités, provende, paramètres environnementaux, pesées) par les vues de l'API, avec le préfixe `SIM-`. Aucune clôture n'est simulée.

## 9. Points ouverts

1. Seuil de vente de la carpe (300 g).
2. Biais de l'ICA sur poissons vivants.
3. Unités des paramètres environnementaux.
