# Prompt : phase 2, calcul des indicateurs (module grossissement)

Prérequis : la phase 1 (pesée) est terminée et intégrée.

## Règles d'exécution (à respecter en premier)

- **Ne pas appliquer les migrations** : ne pas exécuter `migrate`. Générer seulement le fichier de migration (`makemigrations`) et vérifier avec `makemigrations --check`.
- **Ne pas commiter** : aucun `git commit`, `git add`, `git stash` ni changement de branche. Laisser toutes les modifications dans l'arbre de travail.
- **Ne pas exécuter la collection Postman** : la base du développeur n'est pas migrée. Seuls les tests unitaires **sans base de données** peuvent être lancés.
- Le développeur appliquera la migration, exécutera les tests Postman et commitera lui-même après vérification.

## Contexte à lire d'abord

1. `REGLES_DE_GESTION_FISH_FARM.md`
2. `CONTEXTE_INDICATEURS_GROSSISSEMENT.md` (version confirmée ; sections 2, 5 et 6 en priorité)
3. Le code existant, y compris la phase 1 déjà réalisée (`Pesee`, `PeseeLot`, service `creer_pesee`), le registre `effectifs` (fonction d'effectif valable à une date donnée), le journal de provende de l'app `provende`, et la collection Postman actuelle.

Reprendre les conventions du code existant (services `@transaction.atomic`, `select_for_update()`, `ValidationError({"champ": "message"})`, serializers, permissions). Vérifier les noms exacts des modèles et des champs avant de les utiliser.

## Règles métier pour deux lots dans le même bassin

Un bassin peut contenir au plus deux lots EN_COURS d'espèces différentes. Les règles suivantes sont confirmées par l'exploitant :

- La provende est tracée **par bassin**, une seule fois. Les deux lots sont réputés consommer **les mêmes provendes** (mêmes types, mêmes distributions). Ne jamais saisir, copier ni dupliquer une distribution par lot.
- Chaque lot a **ses propres paramètres biologiques** : poids moyen, biomasse, GMQ, IC. Chaque lot a ses propres instantanés.
- Seule la **quantité** de provende est répartie entre les lots, **au prorata de leurs biomasses de référence**, pour permettre le calcul de l'IC de chaque lot (section 4).
- La biomasse et l'IC portent sur les poissons vivants uniquement.

## Périmètre

À faire uniquement :
1. Fonctions de calcul **pures** (sans accès à la base).
2. Modèle `IndicateurLot` et sa migration (fichier généré, non appliqué).
3. Service de calcul et de recalcul des instantanés, branché sur la création, la modification et la suppression d'une pesée.
4. Exposition en lecture seule des indicateurs dans la réponse des pesées.
5. Tests unitaires des fonctions pures (écrits et exécutés) et tests Postman du dossier « Indicateurs » (écrits, non exécutés).

Hors périmètre : endpoint de suivi et courbes, clôture, alertes, service IA, données simulées, frontend. Ne pas modifier le journal de provende, le journal de mortalité ni l'app `effectifs`.

## 1. Fonctions pures

Dans un module dédié (par exemple `grossissement/calculs.py`), sans import de modèle ni accès à la base. Utiliser `Decimal`, avec arrondi demi supérieur appliqué **uniquement au résultat final**, jamais sur les valeurs intermédiaires.

- `biomasse_kg(effectif_vivant, poids_moyen_g)` = `effectif_vivant × poids_moyen_g / 1000`, arrondie à 3 décimales.
- `gmq(poids_moyen_debut, poids_moyen_fin, nombre_jours)` = `(fin − début) / jours`, arrondi à 2 décimales ; `None` si `nombre_jours` ≤ 0. Un résultat négatif est valide.
- `ic(provende_kg, biomasse_debut_kg, biomasse_fin_kg)` = `provende_kg / (biomasse_fin_kg − biomasse_debut_kg)`, arrondi à 2 décimales ; `None` si le gain de biomasse est ≤ 0, ou si `provende_kg` vaut 0 (aucune distribution enregistrée : indicateur non interprétable).
- `repartir_provende(quantite_kg, biomasses_reference)` : répartit la quantité entre des lots au prorata de leurs biomasses de référence (dictionnaire identifiant du lot vers biomasse).
  - Chaque part est arrondie à 3 décimales (kg), **sauf celle du lot de plus grande biomasse**, qui reçoit le reste, de sorte que la somme soit exactement égale à la quantité.
  - En cas d'égalité des biomasses, le lot dont l'identifiant est le plus petit reçoit le reste.
  - Un seul lot reçoit 100 %.
  - Si la somme des biomasses est 0, retourne une répartition vide (rien n'est alloué).

Les valeurs non calculables sont `None`, jamais zéro.

## 2. Modèle `IndicateurLot`

Un instantané par `PeseeLot`.

- `pesee_lot` : OneToOne vers `PeseeLot` (related_name `indicateur`, `on_delete=CASCADE`).
- `effectif_vivant` : entier (valeur de la ligne de pesée).
- `poids_moyen` : décimal (g).
- `biomasse_kg` : décimal, 3 décimales.
- `gmq_periode`, `gmq_cumule` : décimaux, 2 décimales, nullables.
- `provende_periode_kg` : décimal, 3 décimales, jamais nul (0 si rien).
- `ic_periode`, `ic_cumule` : décimaux, 2 décimales, nullables.
- `jour_de_cycle` : entier = nombre de jours entre `date_entree` du lot et la date de la pesée.
- `updated_at`.

Les valeurs sont toujours recalculées par le service ; aucune n'est acceptée en entrée.

## 3. Règles de calcul (pour un lot, à la pesée n)

Point de départ (pesée 0) : `date_entree`, `poids_moyen_initial`, `effectif_initial`, `biomasse_0 = biomasse_kg(effectif_initial, poids_moyen_initial)`.

- `poids_moyen_n` : valeur de la ligne de pesée. `effectif_vivant_n` : valeur copiée dans la ligne de pesée (lue dans le registre à la date de la pesée).
- `biomasse_n = biomasse_kg(effectif_vivant_n, poids_moyen_n)`.
- `gmq_periode_n` : entre la pesée n−1 (ou la pesée 0 pour la première) et la pesée n, divisé par le nombre de jours entre leurs dates.
- `gmq_cumule_n` : entre la pesée 0 et la pesée n, divisé par les jours depuis `date_entree` (`None` si 0 jour).
- `provende_periode_n` : provende allouée au lot pendant la période (section 4).
- `ic_periode_n = ic(provende_periode_n, biomasse_n−1, biomasse_n)`.
- `ic_cumule_n = ic(somme des provendes allouées de la pesée 1 à n, biomasse_0, biomasse_n)`.
- IC et biomasse portent sur les poissons vivants uniquement ; la biomasse des morts n'est pas rajoutée au gain.
- Aucune interpolation entre deux pesées.

## 4. Provende allouée au lot

Convention de période : une distribution compte dans la période n si sa **date locale** (fuseau du projet) est `> date de la pesée n−1` et `≤ date de la pesée n`. Pour la première période, la borne basse est `≥ date_entree`.

Répartition, **jour par jour** (regrouper les distributions du bassin par date locale, puis allouer la quantité totale du jour) :
1. Retenir les lots du bassin présents ce jour D : `date_entree ≤ D`, et non clôturés à D (si le champ `date_cloture` existe : nul ou `≥ D`).
2. Pour chaque lot, biomasse de référence = `effectif vivant à D` (registre, valable à la date) × **dernier poids moyen connu avant D** : poids moyen de la dernière pesée dont la date est **strictement antérieure** à D, sinon `poids_moyen_initial`.
3. Allouer la quantité du jour avec `repartir_provende` ; un seul lot présent reçoit 100 %.
4. Si aucun lot n'est présent ou si la somme des biomasses est 0, la provende du jour n'est allouée à personne.

Ne compter que les distributions valides du journal existant (inspecter le modèle : statut, annulation, etc.). La quantité doit être en kilogrammes : si l'unité du modèle est différente, s'arrêter et poser la question.

Performance : pas de requête par distribution. Charger les distributions de la période en une requête, calculer l'effectif vivant par lot et par jour, puis agréger en mémoire.

## 5. Service de recalcul

- `recalculer_indicateurs_bassin(bassin)` : pour tous les lots du bassin ayant des pesées, reparcourt leurs pesées par ordre chronologique et crée ou met à jour (`update_or_create`) chaque `IndicateurLot`. L'opération est idempotente. Le recalcul porte sur **tous les lots du bassin**, car la répartition de la provende d'un lot dépend des poids et des effectifs de l'autre lot : une nouvelle pesée de l'un peut modifier les instantanés de l'autre.
- Appel dans la **même transaction** : à la fin de `creer_pesee`, à la fin de la modification d'une pesée, et après la suppression d'une pesée (les instantanés de la pesée supprimée disparaissent avec ses lignes).
- Ne pas modifier le comportement ni les contrôles existants de la phase 1.

## 6. Lecture

Ajouter dans la sortie des pesées, par ligne, un objet `indicateurs` en lecture seule : `biomasse_kg`, `gmq_periode`, `gmq_cumule`, `provende_periode_kg`, `ic_periode`, `ic_cumule`, `jour_de_cycle`. Aucun nouvel endpoint.

## 7. Tests

### Tests unitaires sans base de données (à écrire et exécuter)

1. Un seul lot. `effectif_initial` 1000, `poids_moyen_initial` 20 g, entrée le 2026-01-01 (biomasse 20 kg).
   - Pesée 1 le 2026-01-31 : effectif 980, poids moyen 45 g. Biomasse 44,100 kg. GMQ période et cumulé 0,83. Provende de la période 50 kg. IC période et cumulé 2,07.
   - Pesée 2 le 2026-03-02 : effectif 950, poids moyen 80 g. Biomasse 76,000 kg. GMQ période 1,17, cumulé 1,00. Provende de la période 90 kg. IC période 2,82, IC cumulé 2,50 (140 kg pour un gain de 56 kg).
2. Gain de biomasse négatif ou nul : IC `None`. Provende de la période à 0 : IC `None`.
3. Répartition :
   - lot A 1000 poissons à 50 g, lot B 500 poissons à 100 g, 10 kg : 5,000 et 5,000 ;
   - lot A 800 à 50 g (40 kg), lot B 400 à 150 g (60 kg), 10 kg : 4,000 et 6,000 ;
   - biomasses 42,75 (A) et 40,00 (B), 90 kg : A 46,495 et B 43,505 ; 30 kg : A 15,498 et B 14,502 ;
   - biomasses 42,75 (A) et 20,00 (B), 90 kg : A 61,315 et B 28,685 ; 30 kg : A 20,438 et B 9,562 ;
   - un seul lot : 100 % ; somme des biomasses nulle : répartition vide ;
   - la somme des parts est toujours égale à la quantité.
4. Pas de jours entre deux dates : GMQ `None`.

### Tests Postman (à écrire, non exécutés)

Dossier **« Indicateurs »**, indépendant, qui crée son propre bassin, ses lots, ses effectifs, ses mortalités et ses distributions de provende. Les dates sont fixées dans le passé. Si `date_entree` ne peut pas être fixée dans le passé via l'API, s'arrêter et proposer une solution avant de continuer. Si les règles des journaux (plafond de distributions par jour, etc.) empêchent un scénario, répartir les quantités sur plusieurs distributions et le signaler dans le compte rendu. Les assertions comparent les valeurs aux décimales indiquées.

#### Scénario 1 : un lot seul
Reprendre le cas 1 des tests unitaires : effectif 1000 à 20 g le 2026-01-01 ; mortalités datées avant chaque pesée pour obtenir 980 puis 950 ; provende du bassin de 50 kg le 2026-01-15 et de 90 kg le 2026-02-15 ; pesée 1 le 2026-01-31 (98 poissons, 4 410 g) ; pesée 2 le 2026-03-02 (95 poissons, 7 600 g). Valeurs attendues : celles du cas 1.

#### Scénario 2 : deux lots d'espèces différentes dans le même bassin, avec recalcul croisé
Mise en place :
- un bassin de grossissement actif, une seule provende (un seul type) ;
- lot A (espèce 1) : effectif 1 000, poids moyen initial 20 g, entrée le 2026-01-01 ;
- lot B (espèce 2) : effectif 500, poids moyen initial 40 g, entrée le 2026-01-01 ;
- provende du bassin (une seule saisie par distribution, jamais par lot) : 40 kg le 2026-01-10, 60 kg le 2026-01-20, 90 kg le 2026-02-10, 30 kg le 2026-02-20 ;
- mortalité du lot A : 50 poissons le 2026-02-05 (cause « inconnue »).

Séquence et vérifications :

| Étape | Action | Vérification |
|---|---|---|
| 1 | Pesée du lot A seul le 2026-01-31 : 100 poissons, 4 500 g (45 g) | A : `jour_de_cycle` 30, `biomasse_kg` 45,000, `gmq_periode` et `gmq_cumule` 0,83, `provende_periode_kg` 50,000, `ic_periode` et `ic_cumule` 2,00 |
| 2 | Pesée du lot A seul le 2026-03-02 : 95 poissons, 8 550 g (90 g) | A : effectif vivant 950, `biomasse_kg` 85,500, `gmq_periode` 1,50, `gmq_cumule` 1,17, `provende_periode_kg` **81,753** (le lot B n'a pas encore de pesée : référence 20,00 kg), `ic_periode` 2,02 |
| 3 | Pesée du lot B seul le 2026-01-31 : 50 poissons, 4 000 g (80 g) | B : `biomasse_kg` 40,000, `gmq_periode` 1,33, `provende_periode_kg` 50,000, `ic_periode` 2,50. **Recalcul croisé** : la pesée 2 du lot A est recalculée, `provende_periode_kg` passe à **61,993**, `ic_periode` à 1,53, `ic_cumule` à 1,71 |
| 4 | Pesée du lot B seul le 2026-03-02 : 50 poissons, 7 000 g (140 g) | B : `biomasse_kg` 70,000, `gmq_periode` 2,00, `gmq_cumule` 1,67, `provende_periode_kg` 58,007, `ic_periode` 1,93, `ic_cumule` 2,16 |
| 5 | Lecture des deux lots | Les instantanés de A et de B sont distincts. Somme des provendes allouées sur la période 1 : 100,000 ; sur la période 2 : 120,000 (61,993 + 58,007). Le journal de provende ne contient que les 4 distributions du bassin |

Valeurs de référence pour la vérification manuelle (étape 3, période 2 du lot A, effectif vivant de A 950 après la mortalité du 2026-02-05) : biomasses de référence A = 42,75 kg et B = 40,00 kg, soit 90 kg répartis en 46,495 (A) et 43,505 (B), et 30 kg en 15,498 (A) et 14,502 (B).

#### Scénarios complémentaires
3. Gain de biomasse négatif : `ic_periode` à `null`.
4. Lot sans provende sur la période : `provende_periode_kg` à 0 et IC à `null`.
5. Première pesée le jour même de `date_entree` : GMQ à `null`.
6. Modification de la dernière pesée : instantané recalculé, vérifié à la main dans le test.
7. Suppression de la dernière pesée : son instantané disparaît, l'instantané précédent reste inchangé ; la nouvelle pesée saisie ensuite repart de la pesée précédente restante.

## Contraintes

- Ne pas modifier l'app `effectifs`, ses signaux ni les journaux existants.
- Pas de nouvelle dépendance.
- Ne pas casser les tests Postman existants de la phase 1.

## Compte rendu attendu

- Liste des fichiers créés ou modifiés, nom du fichier de migration généré (non appliqué).
- Résultat des tests unitaires exécutés.
- Hypothèses prises, notamment : borne des périodes de provende, poids moyen de référence strictement antérieur, IC à `None` quand la provende de la période est nulle, unité de la quantité de provende.
- Commandes que le développeur doit lancer ensuite : application de la migration, exécution de la collection Postman, puis commit.
