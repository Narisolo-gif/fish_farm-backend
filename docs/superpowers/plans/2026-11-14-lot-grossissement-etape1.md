# Lot de grossissement : étape 1, plan d'implémentation

> **Pour les agents :** sous-skill requis : superpowers:subagent-driven-development (recommandé) ou superpowers:executing-plans pour exécuter ce plan tâche par tâche. Les étapes utilisent des cases à cocher (`- [ ]`).

**Objectif :** compléter `LotGrossissement` avec les règles de création (deux lots maximum, espèces différentes, pas de hapa) et une clôture complète (poids final, effectif final, mouvement `SORTIE`, stade `CLOTUREE`), plus la saisie du poids initial par PATCH.

**Architecture :** tout reste dans l'app `grossissement` : modèle, receiver, services, serializers, viewset. Le registre `effectifs` n'est pas modifié. Il est lu par `effectif_vivant(bassin_id, espece, a_la_date)` et alimenté par `enregistrer_mouvement_operation`.

**Stack :** Django 5.2, DRF, PostgreSQL (Docker), tests `manage.py test`, Postman et newman.

**Spec :** `docs/superpowers/specs/2026-11-14-lot-grossissement-etape1-design.md`

## Contraintes globales
- Les noms du code existant priment : `ACTIF`, `date_mise_en_place`, `poids_initial_g`, `motif_cloture`, `observation`. On ajoute seulement `effectif_final` et `poids_final_g`.
- Libellés, messages d'erreur et commentaires en français.
- Les services sont en `@transaction.atomic`, avec `select_for_update()` sur Lot et Bassin. Les erreurs sont des `rest_framework.exceptions.ValidationError({"<champ_api>": "..."})`.
- `effectifs` n'importe jamais `grossissement`.
- Commande des tests : `docker compose exec backend python manage.py test apps.grossissement apps.effectifs`

## Points de revue
1. Clôture d'un lot dont l'effectif vivant est déjà 0 : la clôture réussit avec `effectif_final = 0`, sans mouvement `SORTIE` (un mouvement de quantité 0 est interdit). Testé en tâche 2.
2. `date_cloture` antérieure à des mortalités déjà saisies : le `SORTIE` de l'effectif vivant à cette date rendrait le solde négatif plus tard. Le contrôle cumulatif du registre lève alors une erreur. On attend un 400 et aucune écriture. Testé en tâche 2.
3. Deux lots de même espèce, quand l'ancien est clôturé : le second est accepté (la place est libérée). Testé en tâche 3.
4. Annulation d'une sortie après la clôture du lot : déjà refusée par le receiver existant, à ne pas casser. Couvert par `test_annulation_refuse_poids_initial_ou_lot_cloture_sans_changement`, à adapter à la nouvelle signature.
5. `PATCH poids_initial_g` ≤ 0 : 400 avec un message sur `poids_initial_g`. Testé en tâche 4.

---

### Tâche 1 : champs `effectif_final` et `poids_final_g`

**Fichiers :**
- Modifier : `apps/grossissement/models.py`, `apps/grossissement/serializers.py` (`LotGrossissementSerializer.Meta.fields`, en lecture seule)
- Créer : `apps/grossissement/migrations/0002_lot_effectif_poids_final.py` (via `makemigrations grossissement`)

**Interfaces produites :** `LotGrossissement.effectif_final: PositiveIntegerField(null=True, blank=True)` et `LotGrossissement.poids_final_g: DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)`. Nouvelle contrainte `lot_gross_poids_final_positif` : `Q(poids_final_g__isnull=True) | Q(poids_final_g__gt=0)`. Les deux champs sont exposés dans l'API en lecture seule.

- [ ] Ajouter les champs et la contrainte, puis lancer `docker compose exec backend python manage.py makemigrations grossissement`. Attendu : `0002_...` créé.
- [ ] Lancer `migrate`, puis la suite de tests. Attendu : tout passe (aucun comportement modifié).
- [ ] Commit `feat(grossissement): champs effectif_final et poids_final_g`.

### Tâche 2 : clôture complète

**Fichiers :**
- Modifier : `apps/grossissement/services.py` (`cloturer_lot_grossissement`), `apps/grossissement/serializers.py` (`ClotureLotGrossissementSerializer`), `apps/grossissement/viewsets.py` (action `cloturer`)
- Tests : `apps/grossissement/tests/test_lot_grossissement.py`

**Interfaces :**
- Consomme : `apps.effectifs.calculs.effectif_vivant(bassin_id, espece, a_la_date)` et `apps.effectifs.services.enregistrer_mouvement_operation(operation=lot_grossissement, bassin=..., espece=..., type_mouvement=MouvementEffectif.TypeMouvement.SORTIE, quantite=..., date_evenement=date_cloture, saisi_par=utilisateur, lot_origine=lot_origine, lot_grossissement=lot_origine)`. Le champ `lot_grossissement` du registre est une FK vers `Lot`.
- Produit : `cloturer_lot_grossissement(lot_id, *, motif: str, poids_final_g: Decimal, utilisateur, date_cloture: date | None = None) -> LotGrossissement`.
- Serializer : `motif` (obligatoire, non vide), `poids_final_g` (obligatoire, > 0, `max_digits=8`, `decimal_places=2`), `date_cloture` (DateField facultatif).

Ordre dans le service :
1. Verrouiller le lot, puis le bassin, puis `lot_origine`.
2. Vérifier `statut == ACTIF`.
3. Valider `date_cloture` (défaut : `timezone.localdate()`). Elle doit être ≥ `localtime(date_mise_en_place).date()` et ≤ aujourd'hui. Erreur sur la clé `date_cloture`.
4. Valider `poids_final_g > 0`.
5. Calculer `effectif = effectif_vivant(...)`. Si `effectif > 0`, écrire le mouvement `SORTIE`.
6. Renseigner `statut`, `date_cloture`, `motif_cloture`, `poids_final_g` et `effectif_final`, puis `lot_origine.stade = Lot.Stade.CLOTUREE`.

- [ ] **Écrire les tests en échec**, de façon que chaque assertion corresponde à une règle :
  - `test_cloture_exige_motif_et_ne_peut_etre_reouverte` : à adapter, chaque POST envoie `"poids_final_g": "250.00"`.
  - `test_cloture_exige_poids_final_positif` : le POST sans `poids_final_g` renvoie 400 avec la clé `poids_final_g` ; `"0"` renvoie aussi 400 ; le lot reste `ACTIF`.
  - `test_cloture_evacue_effectif_et_cloture_le_lot_origine` : un lot de 10, une mortalité de 2 via le service `apps.effectifs.services.enregistrer_evenement` (ou `POST /api/v1/effectifs/evenements-mortalite-perte/`, sans hapa), puis la clôture. On attend `effectif_final == 8` et `poids_final_g == "250.00"`. Un `MouvementEffectif` `SORTIE` de quantité 8 existe dans le bassin de destination. `effectif_vivant(bassin_destination.pk, CARPE) == 0`. Le stade de `Lot` vaut `CLOTUREE`.
  - `test_cloture_refuse_date_future_ou_avant_mise_en_place` : `today + 1 jour` renvoie 400, `date_mise_en_place - 1 jour` renvoie 400 ; dans les deux cas la clé est `date_cloture`, et aucun mouvement n'est créé.
  - `test_cloture_effectif_nul_sans_mouvement_sortie` (point de revue 1) : mortalité totale avant la clôture. On attend un 200, `effectif_final == 0` et aucun `SORTIE` supplémentaire.
  - `test_cloture_avant_mortalites_ulterieures_refusee` (point de revue 2) : mise en place à J-5, mortalité de 2 à J-1, clôture datée de J-3. On attend un 400, le statut reste `ACTIF` et le nombre de mouvements ne change pas. Si le registre accepte ce cas, ajouter dans le service un contrôle explicite (aucun mouvement du bassin et de l'espèce après `date_cloture`) avec l'erreur `{"date_cloture": "Des mouvements existent après la date de clôture."}`.
  - `test_annulation_refuse_poids_initial_ou_lot_cloture_sans_changement` : remplacer l'appel par `cloturer_lot_grossissement(gross_b.pk, motif="Fin du cycle", poids_final_g=Decimal("250"), utilisateur=self.user)`.
- [ ] Lancer `docker compose exec backend python manage.py test apps.grossissement`. Attendu : les nouveaux tests échouent.
- [ ] Implémenter le service, le serializer et l'action. L'action passe `utilisateur=request.user`.
- [ ] Relancer les tests. Attendu : tout passe. Vérifier aussi `apps.effectifs`.
- [ ] Commit `feat(grossissement): clôture avec poids final, effectif final et évacuation`.

### Tâche 3 : règles de création (bassin sans hapa, deux lots maximum, espèces différentes)

**Fichiers :**
- Modifier : `apps/grossissement/receivers.py` (`creer_lot_apres_sortie_validee`)
- Tests : `apps/grossissement/tests/test_lot_grossissement.py`

**Interfaces :** une fonction `_verifier_capacite_bassin(bassin, espece)` dans `receivers.py` (ou dans `services.py` si on veut la réutiliser), appelée avant `LotGrossissement.objects.create`. Le bassin est relu avec `Bassin.objects.select_for_update().get(pk=sortie.bassin_destination_id)`. Messages, tous sur la clé `id_bassin_destination` :
- `"Le bassin de grossissement ne doit contenir aucun hapa actif."` : vrai si `bassin.hapas.filter(statut=Hapa.Statut.ACTIF).exists()`.
- `"Le bassin contient déjà deux lots de grossissement en cours."` : vrai si au moins 2 lots `ACTIF`.
- `"Le bassin contient déjà un lot en cours de la même espèce."`

Le receiver s'exécute dans la transaction de `valider_sortie`. Une erreur annule donc aussi les mouvements et la sortie, qui reste `BROUILLON`.

- [ ] **Écrire les tests en échec.** Adapter d'abord `_creer_lot(reference, *, parcours=None, espece=Lot.Espece.CARPE)` (espèce de la reproduction et du lot). Pour le tilapia, la mise en stock exige un hapa de stock distinct (un hapa = une espèce) : ajouter `self.hapa_stock_b` et un paramètre `hapa` à `_mettre_en_stock`.
  - `test_creation_refuse_meme_espece_dans_le_bassin` : le premier lot CARPE est validé, le second lot CARPE vers le même bassin renvoie 400 (`id_bassin_destination`). La sortie reste `BROUILLON`, le nombre de `MouvementEffectif` est inchangé et le stade du second lot reste `EN_STOCK`.
  - `test_creation_accepte_deux_especes_differentes` : CARPE puis TILAPIA donne 200 et 200, avec deux lots `ACTIF` dans le bassin.
  - `test_creation_accepte_meme_espece_apres_cloture` (point de revue 3) : CARPE, puis clôture, puis nouvelle CARPE. On attend 200.
  - `test_creation_refuse_bassin_avec_hapa_actif` : créer un hapa ACTIF directement par l'ORM dans `bassin_destination`. La validation renvoie 400 avec le message hapa.
  - La règle des deux lots maximum ne peut pas être atteinte avec deux espèces seulement. Pas de test API : un test unitaire de `_verifier_capacite_bassin` avec trois lots créés par l'ORM, ou on garde le contrôle sans test (choix de l'implémenteur, à noter dans le commit).
- [ ] Lancer les tests. Attendu : les nouveaux tests échouent.
- [ ] Implémenter.
- [ ] Lancer `apps.grossissement` et `apps.effectifs`. Attendu : tout passe.
- [ ] Commit `feat(grossissement): deux lots d'espèces différentes par bassin, sans hapa`.

### Tâche 4 : saisie du poids initial par PATCH

**Fichiers :**
- Modifier : `apps/grossissement/serializers.py` (ajouter `poids_initial_g` aux champs modifiables), `apps/grossissement/services.py` (`modifier_lot_grossissement`)
- Tests : `apps/grossissement/tests/test_lot_grossissement.py`

**Interfaces :** `modifier_lot_grossissement(lot_id, data)` accepte `poids_initial_g` (> 0, sinon `{"poids_initial_g": "Le poids initial doit être strictement positif."}`). Ne mettre à jour que les champs présents dans `data`. Aujourd'hui, `update_fields` écrit toujours les deux champs ; c'est sans conséquence, mais à rendre exact. L'étape 2 verrouillera ce champ après la première pesée.

- [ ] **Tests en échec.** `test_patch_poids_initial` : `"12.50"` donne 200 et `poids_initial_g == "12.50"` ; `"0"` donne 400 (`poids_initial_g`) ; sur un lot `CLOTURE`, on attend 400 (`statut`). Adapter `test_patch_modifie_champs_autorises_et_refuse_champs_figes` si ce test vérifie que `poids_initial_g` est figé.
- [ ] Implémenter, puis lancer les tests. Attendu : tout passe.
- [ ] Commit `feat(grossissement): saisie du poids initial par PATCH`.

### Tâche 5 : Postman, dossier 10

**Fichiers :** `tests/postman/pisciculture_e2e.postman_collection.json`, `tests/postman/README_TEST.md`

- [ ] Ajouter `"poids_final_g": "250.00"` au corps des requêtes 10.13 à 10.15. Avant 10.13, ajouter : « Saisir poids initial du lot C » (PATCH `poids_initial_g`, 200) et « Refuser clôture sans poids final » (400, clé `poids_final_g`).
- [ ] Après 10.14, vérifier dans le test de la réponse `effectif_final` = solde GRO lu en 10.08, `poids_final_g == "250.00"`. Mettre à jour 12.07 : le solde final GRO vaut maintenant 0, à cause du `SORTIE` de clôture.
- [ ] Ajouter « Refuser seconde sortie CARPE vers GRO » seulement si le scénario dispose d'un lot CARPE sortable vers GRO pendant que C est actif. Sinon, le noter dans les écarts connus du README (les règles restent couvertes par les tests Django).
- [ ] Remettre la base à zéro (`docker compose down -v && docker compose up -d --build && docker compose exec backend python manage.py migrate`), puis lancer newman (commande du README). Attendu : aucune nouvelle assertion en échec par rapport aux écarts connus listés.
- [ ] Commit `test(postman): clôture de grossissement avec poids et évacuation`.
