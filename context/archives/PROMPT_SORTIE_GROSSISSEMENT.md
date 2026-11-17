# Prompt : sortie de stock vers le grossissement et création du lot (une seule transaction)

À réaliser **avant la phase 4**. Ce prompt **remplace** `PROMPT_CORRECTIF_POIDS_INITIAL.md` (il le recouvre). Il ne touche ni aux pesées de suivi, ni au calendrier, ni aux indicateurs.

## Règles d'exécution (à respecter en premier)

- **Ne pas appliquer les migrations** : ne pas exécuter `migrate`. Générer seulement les fichiers de migration (`makemigrations`) et vérifier avec `makemigrations --check`.
- **Ne pas commiter** : aucun `git commit`, `git add`, `git stash` ni changement de branche.
- **Ne pas exécuter la collection Postman** : la base du développeur n'est pas migrée. Seuls les tests unitaires **sans base de données** peuvent être lancés.
- Le développeur appliquera les migrations, exécutera les tests Postman et commitera lui-même.

## Contexte à lire d'abord

1. `REGLES_DE_GESTION_FISH_FARM.md`
2. `CONTEXTE_INDICATEURS_GROSSISSEMENT.md` (version confirmée)
3. Le code existant : app `stocks` (mise en stock, sortie de stock, services, viewsets, signaux `sortie_stock_validee` et `sortie_stock_avant_annulation`), `lots` (`Lot`, `Lot.Stade`), `effectifs` (`enregistrer_mouvement_operation`, `MouvementEffectif`), `grossissement` (`LotGrossissement`, `receivers.py`), `bassins`, les permissions existantes, le blocage de saisie et la collection Postman.

**Avant d'écrire du code**, relever l'état réel du code sur ces points et signaler tout écart avec ce prompt : modèle et service de la sortie de stock, valeurs de `Lot.Stade`, représentation de la « clôture » d'un stock, mécanisme d'écart d'effectif (`justification_ecart`, `_verifier_justification`), contrôles du bassin, format de la référence des lots. Reprendre les conventions existantes (services `@transaction.atomic`, `select_for_update()`, `ValidationError({"champ": "message"})`, serializers, permissions). Si le code diffère de ce qui est décrit ici de façon à changer la conception, s'arrêter et poser la question.

## Règles de gestion confirmées

**Droits.** Seuls l'Administrateur et le Gestionnaire peuvent créer et valider la sortie vers le grossissement et créer le lot. L'Employé a la **lecture seule** des lots de grossissement. Utilisateur inactif ou non connecté : refusé.

**Pas de brouillon pour le grossissement.** Aucune sortie n'est enregistrée avant la validation finale. Les écrans conservent leurs données côté frontend ; **un seul appel de validation** écrit tout, dans **une seule transaction**. Si une validation échoue ou si l'utilisateur annule, rien n'est écrit et l'état reste exactement celui d'avant. Les autres finalités de sortie (traitement, reproduction, etc.) **ne changent pas**.

**Sortie totale.** La sortie vers le grossissement porte sur la totalité du stock : un stock donne un lot, et le stock est clôturé à la validation.

**Effectif.** Le stock est décrémenté de la **quantité sortie**. Le lot est créé avec `effectif_initial = effectif_entrant`. `effectif_entrant` peut différer de la quantité sortie dans les deux sens ; tout écart exige une justification (même règle que l'écart d'effectif existant).

**Pesée initiale (P0).** Le lot est pesé avant sa mise en bassin. P0 est un **échantillon** qui ne crée **aucune `Pesee`** (ce n'est pas une pesée du calendrier) ; ses valeurs sont conservées comme trace sur le lot.
- Saisis : `date_pesee_initiale`, `pourcentage_echantillon` (10 par défaut), `nombre_poissons_peses`, `poids_total_g`.
- Calculés : `taille_echantillon_cible = ceil(pourcentage × quantité_sortie / 100)` (minimum 1) ; `poids_moyen_initial = poids_total_g / nombre_poissons_peses`, arrondi demi supérieur à 2 décimales, appliqué **uniquement au résultat final**.
- Le pourcentage cible porte sur la **quantité sortie** (connue avant la saisie de `effectif_entrant`).
- Échantillon non conforme (`nombre_poissons_peses < taille_echantillon_cible`) : refusé tant que `confirmer_echantillon_non_conforme` n'est pas `true` (le frontend affiche une confirmation). Même mécanisme que les pesées de suivi.
- `nombre_poissons_peses` ne dépasse ni la quantité sortie ni `effectif_entrant`.

**Dates.** `date_pesee_initiale ≤ date_mise_en_bassin ≤ aujourd'hui` (date locale du projet). Même jour autorisé, pas de date future. La mise en bassin peut être rétroactive. Le blocage de saisie existant s'applique à ces dates.

**Bassin.** Choisi à l'étape de la sortie, puis repris tel quel. Contrôles : bassin de **grossissement**, **pas d'hapa**, au plus **2 lots `EN_COURS`**, et si un lot est déjà présent, **espèce différente**.

**Lot d'origine.** À la validation, son `stade` passe à « en grossissement » (utiliser la valeur existante de `Lot.Stade` ; si elle n'existe pas, l'ajouter, avec migration non appliquée).

**Référence du lot.** Proposée par le système selon le format existant, modifiable par l'utilisateur, **unique**.

## Travail demandé

### 1. Fonctions pures

Module de calculs existant du grossissement (sans accès à la base, `Decimal`) :
- `taille_echantillon_cible(quantite_sortie, pourcentage)`.
- `poids_moyen_echantillon(poids_total_g, nombre_poissons)`.
- `echantillon_conforme(nombre_poissons_peses, taille_cible)`.

### 2. Service de validation

`valider_sortie_grossissement(...)` dans le service du module (nom à adapter aux conventions), décoré `@transaction.atomic`. Il reçoit un seul jeu de données :

- **sortie** : stock (ou lot d'origine) et bassin de destination ;
- **pesée initiale** : `date_pesee_initiale`, `pourcentage_echantillon`, `nombre_poissons_peses`, `poids_total_g`, `confirmer_echantillon_non_conforme` ;
- **lot** : `reference`, `date_mise_en_bassin`, `effectif_entrant`, `justification_ecart`.

L'utilisateur est fourni par le backend, jamais par le client. Ordre :
1. verrouiller le lot d'origine, le stock et le bassin (`select_for_update()`) ;
2. **toutes les validations d'abord** (droits, état du stock, sortie totale, bassin et ses limites, dates, blocage de saisie, échantillon, écart et justification, unicité de la référence) ;
3. puis les écritures : sortie du stock, clôture du stock, mouvements d'effectif, changement de `stade` du lot d'origine, création de `LotGrossissement`.

Mouvements d'effectif, via `enregistrer_mouvement_operation` et sur le modèle de la mise en stock existante : SORTIE de la quantité sortie du bassin de stock, ENTRÉE de `effectif_entrant` dans le bassin de grossissement, avec `lot_origine`. Reprendre le mécanisme d'écart déjà utilisé par le code ; ne pas en inventer un autre. Le registre reste en ajout seul.

La création du lot est faite **directement par le service, dans la même transaction**. Pour la finalité GROSSISSEMENT, ne plus passer par le signal `sortie_stock_validee` ni par l'ancien `/valider/` (qui attendait `poids_initial_g`). Conserver ce signal pour les autres finalités. Si le code oblige à garder le signal pour le grossissement, s'arrêter et expliquer pourquoi.

**Atomicité vérifiable** : en cas d'erreur à n'importe quelle étape, aucun mouvement n'est créé, le stock n'est pas clôturé, le `stade` du lot d'origine est inchangé, aucun lot de grossissement n'existe.

### 3. Modèle `LotGrossissement`

Ajouter les champs de trace (nullables, pour ne pas casser les lots déjà créés) :
- `date_pesee_initiale` : date ;
- `pourcentage_echantillon_initial` : décimal ;
- `nombre_poissons_peses_initial` : entier positif ;
- `poids_total_initial_g` : décimal, 2 décimales ;
- `justification_ecart` : texte, vide par défaut (si ce champ n'est pas déjà porté ailleurs).

`poids_moyen_initial` garde son nom et son type. Supprimer `poids_initial_g` de toute entrée : **une seule voie d'entrée**. Adapter les tests existants qui l'utilisaient et les lister dans le compte rendu. Générer les migrations, **ne pas les appliquer**.

### 4. API

Reprendre les conventions de routes existantes.
- **Aperçu** (lecture, sans écriture) : retourne les valeurs préremplies (lot d'origine, espèce, quantité sortie, référence proposée, date de pesée si fournie, `taille_echantillon_cible`, `poids_moyen_initial` calculé, `echantillon_conforme`) et exécute les mêmes validations que la validation finale, **sans rien écrire**. Il permet d'afficher les erreurs avant le formulaire du lot. Il réutilise les mêmes fonctions de validation que le service final : pas de code dupliqué.
- **Validation** : un seul `POST` qui appelle le service. Réponse : le lot de grossissement créé, avec les champs de trace en **lecture seule**.
- Les champs de trace ne sont jamais acceptés en écriture sur le lot.
- Permissions : écriture réservée à l'Administrateur et au Gestionnaire ; lecture des lots ouverte à l'Employé.

### 5. Hors périmètre

Frontend, pesées de suivi, indicateurs, calendrier, tolérance de retard, annulation d'une sortie déjà validée, autres finalités de sortie. Ne pas créer de `Pesee`.

## Tests

### Tests unitaires sans base de données (à écrire et exécuter)
- `taille_echantillon_cible(2000, 10)` = 200 ; `(15, 10)` = 2 ; `(5, 10)` = 1 (minimum 1).
- `poids_moyen_echantillon(4000, 200)` = 20,00 ; `(1000, 3)` = 333,33 ; `(1001, 3)` = 333,67.
- `echantillon_conforme(200, 200)` vrai ; `(199, 200)` faux.

### Tests Postman (à écrire, non exécutés)
Dossier **« Sortie grossissement »**, indépendant, qui crée ses propres espèces, lots, stocks et bassins. Utiliser des dates fixes dans le passé. Scénarios :
1. **Cas nominal** : sortie de 2 000 poissons, échantillon de 200 pesant 4 000 g, `effectif_entrant` 2 000. Le lot est créé : `poids_moyen_initial` 20,00, `effectif_initial` 2 000, champs de trace renseignés ; le stock est clôturé ; le lot d'origine passe « en grossissement » ; les mouvements SORTIE et ENTRÉE existent ; aucune `Pesee`.
2. **Écart en moins avec justification** : `effectif_entrant` 1 980, justification fournie : lot créé avec 1 980, écart tracé.
3. **Écart sans justification** : 400, **rien d'écrit** (stock non clôturé, aucun mouvement, aucun lot, `stade` inchangé).
4. **Écart en plus** : `effectif_entrant` supérieur à la quantité sortie avec justification : accepté et tracé ; sans justification : 400.
5. **Échantillon non conforme** : 150 poissons pesés pour une cible de 200 : 400 sans `confirmer_echantillon_non_conforme` ; accepté avec `true`.
6. **Échantillon trop grand** : nombre pesé supérieur à `effectif_entrant` : 400.
7. **Dates** : mise en bassin antérieure à la pesée, date future, date bloquée par le blocage de saisie : 400 chacune.
8. **Bassin** : bassin d'un autre type, bassin avec hapa, bassin déjà à 2 lots EN_COURS, même espèce qu'un lot déjà présent : 400 chacun. Deux lots d'espèces différentes dans le même bassin : accepté.
9. **Référence** : référence déjà utilisée : 400, rien d'écrit.
10. **Aperçu** : retourne les valeurs préremplies et ne crée rien (aucun mouvement, aucun lot, stock inchangé).
11. **Ancien flux** : l'ancien `/valider/` du grossissement avec `poids_initial_g` est refusé. Une sortie d'une autre finalité se valide comme avant.
12. **Permissions** : Administrateur et Gestionnaire OK ; Employé refusé en écriture, 200 en lecture des lots ; sans authentification 401 ; utilisateur inactif refusé.
13. **Lecture seule** : les champs de trace ne peuvent pas être modifiés via l'API du lot.

## Contraintes

- Ne pas modifier l'app `effectifs`, ses signaux ni les journaux existants.
- Pas de nouvelle dépendance.
- Ne pas casser les tests Postman existants ; adapter seulement ceux qui utilisaient l'ancien flux et les lister.
- Code, messages et commentaires en français.

## Compte rendu attendu

- Écarts constatés entre ce prompt et le code (états du lot d'origine, clôture du stock, mécanisme d'écart, signal).
- Fichiers créés ou modifiés ; noms des migrations générées (non appliquées).
- Liste des tests existants adaptés.
- Résultat des tests unitaires.
- Hypothèses prises.
- Commandes que le développeur doit lancer ensuite : application des migrations, exécution de la collection Postman, puis commit.
