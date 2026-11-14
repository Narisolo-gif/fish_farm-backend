# Fish Farm : suivi de grossissement et indicateurs (contexte de développement)

Ce document complète `REGLES_DE_GESTION_FISH_FARM.md` (dont la section 9 décrit la sortie vers le grossissement et la création du lot). Il décrit les règles retenues, le modèle de données, les formules des indicateurs et les fonctionnalités attendues. Les points marqués **[À CONFIRMER]** n'ont pas été tranchés : proposer la valeur par défaut indiquée, ne pas la figer sans validation.

## 0. Consignes pour l'assistant de code

- Français pour les libellés, messages d'erreur et commentaires.
- Avant de coder, lire les modèles et services existants (`lots`, `bassins`, `effectifs`, `provende`, `stocks`) et réutiliser leurs conventions : services `@transaction.atomic`, `select_for_update()`, `ValidationError({"champ": "message"})`, serializers avec `id_lot` / `id_bassin`, utilisateur et dates posés par le serveur, classe de permissions par module.
- Respecter la règle de dépendance : l'app `effectifs` n'importe aucune app métier. L'app de grossissement importe `effectifs` ; le registre ne la notifie que par signaux.
- Ne rien implémenter hors périmètre demandé. En cas de doute métier, poser la question.
- Les tests Postman sont écrits par l'assistant, un dossier par fonctionnalité, et exécutés par le développeur. L'assistant ne lance que les tests unitaires sans base de données, n'applique pas les migrations et ne commite pas.
- Vocabulaire : « ICA » (indice de conversion alimentaire, FCR) dans les libellés visibles ; les noms de code conservent `ic_periode` et `ic_cumule`.

## 1. Décisions confirmées pour cette partie

- Les indicateurs biologiques (poids moyen, GMQ, ICA, biomasse) sont calculés **par lot**. Deux lots peuvent partager un bassin, mais chacun a ses propres indicateurs.
- La provende est enregistrée **par bassin**. Pour calculer l'ICA de chaque lot, elle est répartie automatiquement selon la biomasse vivante de chaque lot (section 6). Les deux lots d'un bassin consomment les mêmes provendes, tracées une seule fois ; chacun a ses paramètres biologiques.
- La pesée se fait sur un **échantillon** qui remplace l'ancien recomptage complet. Elle ne modifie jamais l'effectif : l'effectif vivant vient toujours du registre.
- Tous les paramètres biologiques portent sur les **poissons vivants** uniquement.
- Le lot est **pesé avant sa mise en bassin** (pesée initiale P0, section 3). Son poids moyen mesuré est le point de départ des indicateurs.

Règles antérieures remplacées :
- « Pesée = recomptage complet par classes de taille » : remplacée par l'échantillonnage.
- « Un seul lot de grossissement par bassin » : remplacée par « au plus deux lots d'espèces différentes ».
- « Création du lot par signal, poids initial fourni avec la validation » : remplacée par le flux unique de la section 3.
- Les hapas restent **interdits** en grossissement.

## 2. Modèle de données

App `grossissement`. Noms à harmoniser avec le code existant.

### LotGrossissement
| Champ | Règle |
|---|---|
| `id` | clé primaire |
| `lot_origine` | FK vers `lots.Lot`, obligatoire. L'espèce se lit via `lot_origine.espece`, sans duplication |
| `bassin` | FK vers `bassins.Bassin`, obligatoire, catégorie GROSSISSEMENT, actif |
| `reference` | code du lot, unique, proposé par le système et modifiable à la création |
| `effectif_initial` | entier > 0, égal à `effectif_entrant` |
| `effectif_final` | entier, nullable, renseigné à la clôture (effectif vivant à cette date) |
| `poids_moyen_initial` | décimal > 0 (g), calculé depuis la pesée initiale |
| `poids_moyen_final` | décimal > 0 (g), nullable, saisi à la clôture |
| `date_entree` | date, obligatoire (date de mise en bassin) |
| `date_pesee_initiale` | date, nullable (trace de P0) |
| `pourcentage_echantillon_initial` | décimal, nullable (trace de P0) |
| `nombre_poissons_peses_initial` | entier > 0, nullable (trace de P0) |
| `poids_total_initial_g` | décimal, 2 décimales, nullable (trace de P0) |
| `justification_ecart` | texte, vide par défaut (écart entre quantité sortie et effectif entrant) |
| `statut` | `EN_COURS` ou `CLOTURE` |
| `date_cloture` | date, nullable |
| `observation_cloture` | texte, facultatif |

Un lot de grossissement correspond à un cycle. Il appartient à un seul bassin. Les champs de trace sont en **lecture seule** dans l'API : seule la validation de la sortie les renseigne.

### Pesee et PeseeLot
- `Pesee` : événement de pesée. Champs : `bassin`, `date`, `utilisateur` (serveur), `observation`. Un événement couvre les lots concernés du bassin.
- `PeseeLot` : une ligne par lot pesé. Champs : `pesee`, `lot_grossissement`, `pourcentage_echantillon` (paramétrable, **10 par défaut**), `effectif_vivant` (lu dans le registre à la date de la pesée, copié pour trace), `taille_echantillon_cible` (= arrondi supérieur du pourcentage appliqué à l'effectif vivant), `nombre_poissons_peses` (entier > 0), `poids_total_g` (décimal > 0), `poids_moyen` (calculé : `poids_total_g / nombre_poissons_peses`).
- Il n'y a **pas** de modèle `PeseeGroupe` : l'échantillon d'un lot est saisi en un seul bloc.

### IndicateurLot (instantané par pesée)
Une ligne par `PeseeLot`, créée et recalculée par le service : `effectif_vivant`, `poids_moyen`, `biomasse_kg`, `gmq_periode`, `gmq_cumule`, `provende_periode_kg`, `ic_periode`, `ic_cumule`, `jour_de_cycle`. Les valeurs non calculables sont `null`, jamais zéro (sauf `provende_periode_kg`, qui vaut 0 s'il n'y a rien).

### ParametreEnvironnemental (par bassin)
Utiliser le modèle existant.

## 3. Sortie de stock et création d'un lot de grossissement

Le flux complet est décrit en section 9 de `REGLES_DE_GESTION_FISH_FARM.md`. Rappel de ce qui touche ce module :

- **Droits** : Administrateur et Gestionnaire seulement ; Employé en lecture.
- **Une seule transaction**, sans brouillon : aperçu en lecture seule, puis un appel de validation qui crée la sortie, clôture le stock, enregistre les mouvements, passe le lot d'origine au stade `EN_GROSSISSEMENT` et crée le `LotGrossissement`. Une erreur ne laisse aucune trace.
- **Pesée initiale P0** : date, pourcentage (10 par défaut), nombre de poissons pesés, poids total. Poids moyen calculé. Cible sur la quantité sortie. Échantillon non conforme : confirmation explicite (`confirmer_echantillon_non_conforme`). Aucune `Pesee` créée.
- **Formulaire du lot** : saisis `reference`, `date_entree`, `effectif_entrant`, `justification_ecart` (obligatoire s'il y a un écart, dans les deux sens) ; préremplis `lot_origine`, espèce, bassin, date de pesée, `poids_moyen_initial`.
- **Dates** : `date_pesee_initiale ≤ date_entree ≤ aujourd'hui`.
- **Contraintes**, vérifiées sous verrou sur le bassin : bassin actif, catégorie GROSSISSEMENT, sans hapa ; au plus **deux lots `EN_COURS`** ; si un lot est déjà présent, espèce **différente** ; `poids_moyen_initial` > 0.
- **Mouvements** : `SORTIE` de la quantité sortie du bassin de stock, `ENTREE` de `effectif_entrant` dans le bassin de grossissement, rattachés au lot d'origine.

## 4. Journal de mortalité et de perte

- Saisie **par lot de grossissement** (le bassin se déduit du lot), car deux lots peuvent partager un bassin et les indicateurs sont par lot.
- Mêmes règles que le journal existant : cause obligatoire dans la liste fermée (dont « inconnue »), refus si l'effectif devient négatif, correction par annulation, blocage de saisie existant.
- Écriture via les services du registre `effectifs`. Si le registre ne sait pas rattacher un mouvement à un lot de grossissement, ajouter une référence générique (type d'opération et identifiant) sans importer l'app `grossissement`.

## 5. Journal de provende

- Réutiliser le journal existant de l'app `provende` (consommation par bassin, `DateTimeField` normalisé à la minute, maximum 3 distributions valides par jour et par bassin, toutes provendes confondues, doublon refusé). Vérifier le nom exact du modèle et du champ de quantité dans le code existant avant d'écrire. La quantité doit être en kilogrammes.
- Aucune saisie par lot : la répartition entre lots est calculée (section 6).
- Une distribution n'est acceptée que si le bassin contient au moins un lot `EN_COURS` à cette date.

## 6. Pesée et formules des indicateurs

### Règles de pesée
- Calendrier : J30, J60, puis tous les 15 jours (J75, J90, …, J180, J195, …), compté depuis `date_entree`, y compris au-delà de J180. Il sert à calculer la **prochaine pesée prévue** et l'alerte de retard ; il ne bloque jamais la saisie.
- **Prochaine pesée** = la première pesée du calendrier dont la date prévue est **strictement postérieure** à la date de la dernière pesée réalisée (J30 s'il n'y en a pas). Cette règle évite un faux retard quand une échéance est sautée puis rattrapée.
- **Retard** : la date de référence est strictement postérieure à la date prévue plus la tolérance (**0 jour par défaut, [À CONFIRMER]**).
- Validations : date ≥ `date_entree`, ≤ aujourd'hui, strictement postérieure à la pesée précédente du lot, lot `EN_COURS`. `nombre_poissons_peses` > 0 et ≤ effectif vivant. Si `nombre_poissons_peses` < taille cible, la pesée est « échantillon non conforme » : alerte et confirmation (`confirmer_echantillon_non_conforme: true`) avant d'accepter la saisie.
- Le poids moyen est **mesuré sur échantillon**. La biomasse est **estimée** (distinction mesuré / estimé à afficher).
- Correction ou suppression : une pesée n'est modifiable ou supprimable que s'il s'agit de la **dernière du lot** et que le lot est `EN_COURS`. Les instantanés sont alors recalculés.

### Formules (pour un lot, à la pesée n)
Calculs en `Decimal`, arrondi demi supérieur appliqué **uniquement au résultat final**.
- `poids_moyen_n` = `poids_total_g` / `nombre_poissons_peses` (g).
- `effectif_vivant_n` = effectif du lot à la date de la pesée, lu dans le registre.
- `biomasse_n` (kg, 3 décimales) = `effectif_vivant_n` × `poids_moyen_n` / 1000.
- Point de départ : `poids_moyen_0` = `poids_moyen_initial` (mesuré à P0) ; `biomasse_0` = `effectif_initial` × `poids_moyen_0` / 1000.
- `gmq_periode_n` (g/j par poisson, 2 décimales) = (`poids_moyen_n` − `poids_moyen_n-1`) / nombre de jours entre les deux pesées ; `null` si 0 jour.
- `gmq_cumule_n` = (`poids_moyen_n` − `poids_moyen_0`) / jours depuis `date_entree`.
- `provende_periode_n` (kg, 3 décimales) = somme des quantités de provende **allouées au lot** pendant la période.
- `ic_periode_n` (ICA, 2 décimales) = `provende_periode_n` / (`biomasse_n` − `biomasse_n-1`). `null` si le gain de biomasse est ≤ 0 ou si la provende de la période est 0 (indicateur non interprétable).
- `ic_cumule_n` = provende cumulée allouée au lot / (`biomasse_n` − `biomasse_0`), même règle de `null`.
- Convention retenue : ICA et biomasse sur poissons **vivants** uniquement ; la biomasse des morts n'est pas rajoutée au gain. Un lot à forte mortalité a donc un ICA plus élevé. **[À CONFIRMER si ce biais est accepté]**
- Aucune interpolation : entre deux pesées, aucune biomasse ni ICA n'est recalculé pour le jour courant. Les dernières valeurs sont affichées avec la date de la pesée d'origine.

### Période de provende
Une distribution compte dans la période n si sa **date locale** (fuseau du projet) est `>` la date de la pesée n−1 et `≤` la date de la pesée n. Pour la première période, la borne basse est `≥ date_entree`.

### Répartition de la provende entre lots d'un même bassin
Jour par jour (distributions du bassin regroupées par date locale D) :
1. Retenir les lots présents ce jour : `date_entree ≤ D` et non clôturés à D.
2. Biomasse de référence de chaque lot = `effectif_vivant` à D × **dernier poids moyen connu avant D** : poids moyen de la dernière pesée dont la date est **strictement antérieure** à D, sinon `poids_moyen_initial`.
3. Allouer la quantité du jour au prorata de ces biomasses (la somme des parts est exactement la quantité). Avec un seul lot, il reçoit 100 %.
4. Si aucun lot n'est présent ou si la somme des biomasses est 0, la provende du jour n'est allouée à personne.
5. Sommer les quantités allouées par lot et par période.

Chaque création, modification ou suppression de pesée recalcule les instantanés de **tous les lots du bassin**, dans la même transaction, car la répartition d'un lot dépend des poids et des effectifs de l'autre.

## 7. Fin de cycle et clôture

- Seuil de fin de cycle : poids moyen ≥ **250 g** à une pesée. Le système signale « fin de cycle possible » (alerte et indicateur sur le tableau de bord) et ne clôture jamais automatiquement.
- Clôture manuelle : saisie de `poids_moyen_final`, `date_cloture`, `observation_cloture`. Contrôles : lot `EN_COURS`, `date_cloture` ≥ date de la dernière pesée, `poids_moyen_final` > 0. `effectif_final` est calculé (effectif vivant à la date de clôture).
- Effets : statut `CLOTURE` ; plus de pesée, de mortalité ni de répartition de provende sur ce lot ; la place est libérée dans le bassin (règle des deux lots).
- Évacuation des poissons : un mouvement `SORTIE` de l'effectif vivant est enregistré dans le registre pour ramener l'effectif du lot à 0. La vente reste hors périmètre. **[À CONFIRMER]**
- `poids_moyen_final` peut différer de la dernière pesée ; le proposer par défaut à la valeur de la dernière pesée **[À CONFIRMER]**.

## 8. Fonctionnalités attendues

1. **Sortie vers le grossissement et création du lot** (section 3).
2. **Paramètres environnementaux** : saisie et liste par bassin, filtre par période.
3. **Événement de pesée** : bassin, date, lots concernés, pourcentage d'échantillon paramétrable, nombre de poissons pesés et poids total par lot ; calcul automatique du poids moyen et des instantanés d'indicateurs.
4. **Journal de mortalité et de perte** (section 4).
5. **Journal de provende** (section 5).
6. **Suivi de cycle d'un lot** :
   - tableau de bord : dernières valeurs de poids moyen, GMQ, ICA, biomasse, effectif vivant, date de la dernière pesée, jour de cycle, prochaine pesée prévue, signal des 250 g, derniers paramètres environnementaux du bassin ;
   - courbes d'évolution du poids moyen, du GMQ et de l'ICA par pesée, avec le point initial (jour 0) : un point de plus à chaque nouvelle pesée jusqu'à la fin du cycle ;
   - clôture du cycle (section 7).

Points d'API à prévoir (noms indicatifs) : `lots-grossissement`, `pesees`, `parametres-environnementaux`, `lots-grossissement/{id}/suivi` (tableau de bord et séries des courbes), `lots-grossissement/{id}/cloturer`, et les deux appels de la sortie vers le grossissement (aperçu en lecture seule, validation).

### Contrat de réponse du suivi (pour le frontend)
`GET lots-grossissement/{id}/suivi/` avec le paramètre facultatif `date_reference` (par défaut la date du jour). Objets : `lot`, `nombre_pesees`, `dernieres_valeurs` (avec `nature` : poids moyen mesuré, biomasse estimée), `point_initial`, `series` (une entrée par pesée), `calendrier` (prochaine pesée, retard), `seuil`, `alertes`, `parametres_environnementaux`, `autres_lots_du_bassin`. Les décimaux sont des nombres JSON ; les valeurs non calculables sont `null`. Un exemple complet sert de données factices au frontend (`docs/contrats/suivi_lot.exemple.json`). Le frontend n'effectue aucun calcul : il affiche les valeurs reçues.

## 9. Permissions

- **Administrateur et Gestionnaire** : tous les droits, dont la sortie vers le grossissement, la création de lot et la clôture.
- **Employé** : lecture des lots et du suivi ; saisie (création et lecture) des pesées, mortalités, provende et paramètres environnementaux ; ni modification ni suppression de pesée, ni clôture, ni création ou validation de lot.
- Utilisateur inactif ou non connecté : refusé.

## 10. Alertes

- Retenues pour le suivi de cycle, calculées à la volée et jamais stockées, uniquement pour un lot `EN_COURS` : `PESEE_EN_RETARD` (niveau attention) et `POIDS_VENTE_ATTEINT` (niveau info).
- Plus tard : mortalité anormale, écart d'effectif. Seuils configurables ; ne pas coder de valeur en dur en dehors du module de constantes (seuil de 250 g, tolérance de retard, calendrier).

## 11. Points ouverts

1. Tolérance de retard de pesée (0 jour par défaut).
2. Biais de l'ICA sur poissons vivants uniquement : accepté ou non.
3. Mouvement `SORTIE` à la clôture et `poids_moyen_final` par défaut.
4. Unité du taux d'oxygène, liste complète des paramètres environnementaux et emplacement de `ParametreEnvironnemental`.
5. Représentation exacte de la « clôture » du stock à la sortie (relevé dans le code).
