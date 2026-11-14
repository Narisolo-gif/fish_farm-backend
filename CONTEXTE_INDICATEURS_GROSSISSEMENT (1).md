# Fish Farm : suivi de grossissement et indicateurs (contexte de développement)

Ce document complète `REGLES_DE_GESTION_FISH_FARM.md` pour la partie grossissement. Il décrit les règles retenues, un modèle de données proposé, les formules des indicateurs et les fonctionnalités attendues. Les points marqués **[À CONFIRMER]** n'ont pas été tranchés : proposer la valeur par défaut indiquée, ne pas la figer sans validation.

## 0. Consignes pour l'assistant de code

- Français pour les libellés, messages d'erreur et commentaires.
- Avant de coder, lire les modèles et services existants (`lots`, `bassins`, `effectifs`, `provende`, `stocks`) et réutiliser leurs conventions : services `@transaction.atomic`, `select_for_update()`, `ValidationError({"champ": "message"})`, serializers avec `id_lot` / `id_bassin`, utilisateur et dates posés par le serveur, classe de permissions par module.
- Respecter la règle de dépendance : l'app `effectifs` n'importe aucune app métier. L'app de grossissement importe `effectifs` ; le registre la notifie uniquement par signaux.
- Ne rien implémenter hors périmètre demandé. En cas de doute métier, poser la question.
- Tests Postman (newman) sur une base Docker/PostgreSQL propre, un dossier par fonctionnalité.

## 1. Décisions confirmées pour cette partie

- Les indicateurs biologiques (poids moyen, GMQ, IC, biomasse) sont calculés **par lot**. Deux lots peuvent partager un bassin, mais chacun a ses propres indicateurs.
- La provende est enregistrée **par bassin**. Pour calculer l'IC de chaque lot, elle est répartie automatiquement selon la biomasse vivante de chaque lot (section 6).
- La pesée se fait sur un **échantillon** qui remplace l'ancien recomptage complet. Elle ne modifie jamais l'effectif : l'effectif vivant vient toujours du registre.
- Tous les paramètres biologiques portent sur les **poissons vivants** uniquement.

Règles antérieures qui sont remplacées :
- « Pesée = recomptage complet par classes de taille » : remplacée par l'échantillonnage.
- « Un seul lot de grossissement par bassin » : remplacée par « au plus deux lots d'espèces différentes ».
- Les hapas restent **interdits** en grossissement.

## 2. Modèle de données proposé

Nouvelle app `grossissement` (ou extension de l'app existante si elle existe déjà). Noms à harmoniser avec le code existant.

### LotGrossissement
| Champ | Règle |
|---|---|
| `id` | clé primaire |
| `lot_origine` | FK vers `lots.Lot`, obligatoire. L'espèce se lit via `lot_origine.espece`, sans duplication |
| `bassin` | FK vers `bassins.Bassin`, obligatoire, catégorie GROSSISSEMENT, actif |
| `effectif_initial` | entier > 0, égal à l'effectif entrant de la sortie de stock |
| `effectif_final` | entier, nullable, renseigné à la clôture (effectif vivant à cette date) |
| `poids_moyen_initial` | décimal > 0 (g), obligatoire |
| `poids_moyen_final` | décimal > 0 (g), nullable, saisi à la clôture |
| `date_entree` | date, obligatoire |
| `statut` | `EN_COURS` ou `CLOTURE` |
| `date_cloture` | date, nullable |
| `observation_cloture` | texte, facultatif |

Un lot de grossissement correspond à un cycle (un cycle par lot). Le lot appartient à un seul bassin.

### Pesee et PeseeLot
- `Pesee` : événement de pesée. Champs : `bassin`, `date`, `utilisateur` (serveur), `observation`. Un événement couvre les lots concernés du bassin.
- `PeseeLot` : une ligne par lot pesé. Champs : `pesee`, `lot_grossissement`, `pourcentage_echantillon` (paramétrable, **10 par défaut**), `effectif_vivant` (lu dans le registre à la date de la pesée, copié pour trace), `taille_echantillon_cible` (= arrondi supérieur du pourcentage appliqué à l'effectif vivant), `nombre_poissons_peses` (entier > 0), `poids_total_g` (décimal > 0), `poids_moyen` (calculé : `poids_total_g / nombre_poissons_peses`). 
- Il n'y a **pas** de modèle `PeseeGroupe` : l'échantillon d'un lot est saisi en un seul bloc (nombre et poids total). Les groupes de pesée pourront être ajoutés plus tard sans changer les formules.

### IndicateurLot (instantané par pesée)
Une ligne par `PeseeLot`, créée par le service de pesée : `effectif_vivant`, `poids_moyen`, `biomasse_kg`, `gmq_periode`, `gmq_cumule`, `provende_periode_kg`, `ic_periode`, `ic_cumule`, `jour_de_cycle`. Les valeurs non calculables sont `null`, jamais zéro.

### ParametreEnvironnemental (par bassin)
utiliser l'existant :

## 3. Création d'un lot de grossissement (validation)

- Le lot est créé par le récepteur du signal `sortie_stock_validee`, quand la finalité est GROSSISSEMENT. `effectif_initial` = effectif entrant.
- Le bassin de destination et `poids_moyen_initial` sont obligatoires. Mécanisme proposé : ils sont fournis avec la validation de la sortie de stock et transmis au récepteur. **[À CONFIRMER]**
- Contraintes, vérifiées sous verrou sur le bassin :
  - bassin actif, catégorie GROSSISSEMENT, sans hapa ;
  - au plus **deux lots EN_COURS** par bassin ;
  - si le bassin contient déjà un lot EN_COURS, l'espèce du nouveau lot doit être **différente** ;
  - `date_entree` renseignée, `poids_moyen_initial` > 0.
- Le registre reçoit un mouvement `ENTREE` rattaché au lot de grossissement.

## 4. Journal de mortalité et de perte

- Saisie **par lot de grossissement** (le bassin se déduit du lot), car deux lots peuvent partager un bassin et les indicateurs sont par lot.
- Règles : utiliser l'existant
- Écriture via les services du registre `effectifs`. Si le registre ne sait pas rattacher un mouvement à un lot de grossissement, ajouter une référence générique (type d'opération et identifiant) sans importer l'app `grossissement`.

## 5. Journal de provende

- Réutiliser le journal existant de l'app `provende` (consommation par bassin, `DateTimeField` normalisé à la minute, maximum 3 distributions valides par jour et par bassin, toutes provendes confondues, doublon refusé). Vérifier le nom exact du modèle et du champ de quantité dans le code existant avant d'écrire.
- Aucune saisie par lot : la répartition entre lots est calculée (section 6).
- Une distribution n'est acceptée que si le bassin contient au moins un lot `EN_COURS` à cette date.

## 6. Pesée et formules des indicateurs

### Règles de pesée
- Fréquence prévue sur un cycle de 6 mois : tous les 30 jours pendant 2 mois, puis tous les 15 jours pendant 4 mois, soit 10 pesées (J30, J60, J75, J90, J105, J120, J135, J150, J165, J180, comptés depuis `date_entree`). Ce calendrier sert à calculer la **prochaine pesée prévue** et l'alerte « pesée en retard » ; il ne bloque jamais la saisie.
- La durée réelle du cycle peut être plus courte ou plus longue. Rythme au-delà de J180 : tous les 15 jours 
- Validations : date ≥ `date_entree`, ≤ aujourd'hui, strictement postérieure à la pesée précédente du lot, lot `EN_COURS`. `nombre_poissons_peses` ≤ effectif vivant et > 0. Si `nombre_poissons_peses` < taille cible, la pesée est marquée « échantillon non conforme » : affichage d'une alerte et popup de validation avant d'accepter le saisie
- Le poids moyen est **mesuré sur échantillon**. La biomasse est **estimée** (distinction mesuré / estimé à afficher).
- Correction : une pesée n'est modifiable ou annulable que s'il s'agit de la dernière du lot et que le cycle est ouvert. Les instantanés sont alors recalculés.

### Formules (pour un lot, à la pesée n)
- `poids_moyen_n` = `poids_total_g` / `nombre_poissons_peses` (g).
- `effectif_vivant_n` = effectif du lot à la date de la pesée, lu dans le registre.
- `biomasse_n` (kg) = `effectif_vivant_n` × `poids_moyen_n` / 1000.
- Point de départ : `poids_moyen_0` = `poids_moyen_initial` ; `biomasse_0` = `effectif_initial` × `poids_moyen_0` / 1000.
- `gmq_periode_n` (g/j par poisson) = (`poids_moyen_n` − `poids_moyen_n-1`) / nombre de jours entre les deux pesées.
- `gmq_cumule_n` = (`poids_moyen_n` − `poids_moyen_0`) / jours depuis `date_entree`.
- `provende_periode_n` (kg) = somme des quantités de provende **allouées au lot** entre les deux pesées (alloc. ci-dessous).
- `ic_periode_n` = `provende_periode_n` / (`biomasse_n` − `biomasse_n-1`). Si le gain de biomasse est ≤ 0, l'IC vaut `null` (non calculable).
- `ic_cumule_n` = provende cumulée allouée au lot / (`biomasse_n` − `biomasse_0`), avec la même règle de `null`.
- Convention retenue : IC et biomasse sur poissons **vivants** uniquement ; la biomasse des morts n'est pas rajoutée au gain. Un lot à forte mortalité a donc un IC plus élevé. **[À CONFIRMER si ce biais est accepté]**
- Aucune interpolation : entre deux pesées, aucune biomasse ni IC n'est affiché pour le jour courant. Les dernières valeurs sont affichées avec la date de la pesée d'origine.

### Répartition de la provende entre lots d'un même bassin
Pour chaque distribution du bassin à une date D :
1. Retenir les lots `EN_COURS` présents dans le bassin à D (`date_entree` ≤ D, non clôturés).
2. Pour chaque lot, calculer une biomasse de référence = `effectif_vivant` à D × dernier poids moyen connu (dernière pesée, sinon `poids_moyen_initial`).
3. Allouer la quantité au prorata de ces biomasses. Avec un seul lot, il reçoit 100 %.
4. Sommer les quantités allouées par lot et par période entre pesées.

## 7. Fin de cycle et clôture

- Seuil de fin de cycle : poids moyen ≥ **250 g** à une pesée. Le système signale « fin de cycle possible » (alerte et indicateur sur le tableau de bord) et ne clôture jamais automatiquement.
- Clôture manuelle : saisie de `poids_moyen_final`, `date_cloture`, `observation_cloture`. Contrôles : lot `EN_COURS`, `date_cloture` ≥ date de la dernière pesée, `poids_moyen_final` > 0. `effectif_final` est calculé (effectif vivant à la date de clôture).
- Effets : statut `CLOTURE` ; plus de pesée, de mortalité ni de répartition de provende sur ce lot ; la place est libérée dans le bassin (règle des deux lots).
- Évacuation des poissons : un mouvement `SORTIE` de l'effectif vivant est enregistré dans le registre pour ramener l'effectif du lot à 0. La vente reste hors périmètre. **[À CONFIRMER]**
- `poids_moyen_final` peut différer de la dernière pesée ; le proposer par défaut à la valeur de la dernière pesée **[À CONFIRMER]**.

## 8. Fonctionnalités attendues

1. **Enregistrement et validation des lots de grossissement** : colonnes obligatoires et bassin de destination (section 3).
2. **Paramètres environnementaux** : saisie et liste par bassin, filtre par période.
3. **Événement de pesée** : bassin, date, lots concernés, pourcentage d'échantillon paramétrable, nombre de poissons pesés et poids total par lot ; calcul automatique du poids moyen et des instantanés d'indicateurs.
4. **Journal de mortalité et de perte** (section 4).
5. **Journal de provende** (section 5).
6. **Suivi de cycle d'un lot** :
   - tableau de bord : dernières valeurs de poids moyen, GMQ, IC, biomasse, effectif vivant, date de la dernière pesée, jour de cycle, prochaine pesée prévue, signal des 250 g, derniers paramètres environnementaux du bassin ;
   - courbes d'évolution du poids moyen, du GMQ et de l'IC par pesée : un point de plus à chaque nouvelle pesée, jusqu'à la fin du cycle ;
   - clôture du cycle (section 7).

Points d'API à prévoir (noms indicatifs) : `lots-grossissement`, `pesees`, `parametres-environnementaux`, `lots-grossissement/{id}/suivi` (tableau de bord et séries des courbes), `lots-grossissement/{id}/cloturer`.

## 9. Permissions

Suivre le modèle des autres modules (Administrateur, Gestionnaire, Employé). Proposition : Administrateur et Gestionnaire ont tous les droits ; l'Employé peut saisir pesées, mortalités, provende et paramètres environnementaux, mais ni clôturer ni créer ou valider un lot. confirmer

## 10. Alertes (seuils paramétrables)

Pesée en retard (écart au calendrier de la section 6), poids de vente atteint (250 g), mortalité anormale, écart d'effectif. Les seuils sont configurables ; ne pas coder de valeur en dur.

## 11. Points ouverts

1. Mécanisme de transmission du bassin et du poids moyen initial à la création du lot (section 3).
2. Unité du taux d'oxygène et liste complète des paramètres environnementaux.
3. Échantillon inférieur à la cible : refus ou avertissement.
4. Correction d'une pesée : limitée à la dernière ou plus large.
5. Rythme de pesée après J180.
6. Biais de l'IC sur poissons vivants uniquement : accepté ou non.
7. Mouvement `SORTIE` à la clôture et `poids_moyen_final` par défaut.
8. Droits de l'Employé.
9. Emplacement de `ParametreEnvironnemental` (app `bassins` ou app dédiée).
