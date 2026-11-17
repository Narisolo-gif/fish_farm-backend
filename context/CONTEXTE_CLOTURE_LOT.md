# Fish Farm : clôture d'un lot de grossissement

Ce document complète `REGLES_DE_GESTION_FISH_FARM.md`, `CONTEXTE_INDICATEURS_GROSSISSEMENT.md` et `CONTEXTE_PHASE4_PAGE_SUIVI.md`. Il décrit la **clôture d'un cycle de grossissement** telle qu'elle est implémentée, et remplace les règles de clôture antérieures (motif obligatoire, poids final saisi, évacuation automatique). Les points marqués **[À CONFIRMER]** ne sont pas tranchés.

## 1. Scénario utilisateur

1. Depuis le **suivi du lot**, l'utilisateur ouvre le lien **Clôturer le lot**.
2. Un formulaire **en lecture seule** résume la clôture : date de clôture, effectif initial (entrant), effectif final, poids moyen initial, poids moyen final, écart de poids.
3. En bas du formulaire : « Confirmez-vous la clôture du lot ? » avec **Valider** et **Annuler**.
4. Sur **Valider** :
   - l'effectif final et le poids moyen final sont enregistrés sur le lot de grossissement ;
   - le lot de grossissement passe à `CLOTURE` (fin du cycle) et le lot d'origine à `CLOTUREE` ;
   - le message « Le cycle du lot GR-AAAA-NNNN a bien été clôturé. » s'affiche ;
   - une boîte de dialogue propose l'**évacuation du bassin** :
     - **Oui** : un mouvement `SORTIE` de l'effectif final est créé ;
     - **Non** : le registre reste intact ; l'évacuation reste possible plus tard.

## 2. Règles de gestion

### Préconditions de la clôture
- Le lot est `ACTIF` (en cours de grossissement).
- Le poids moyen de la **dernière pesée valide** atteint le **seuil de vente de l'espèce** :

  | Espèce | Seuil |
  |---|---|
  | TILAPIA | 250 g |
  | CARPE | 300 g **[À CONFIRMER par l'exploitant]** |

  En dessous du seuil, ou sans aucune pesée, la clôture est **bloquée**. Les seuils sont des constantes (`SEUIL_POIDS_VENTE_G`, `apps/grossissement/calculs.py`).

### Valeurs de la clôture (aucune saisie)
- **Date de clôture** : la date du jour (fuseau Indian/Antananarivo), non modifiable.
- **Poids moyen final** : celui de la dernière pesée valide. L'exploitant est invité à enregistrer une **pesée finale** à jour avant de clôturer.
- **Effectif final** : effectif vivant du registre à la date de clôture.
- **Observation** : facultative (le seuil atteint est le motif de la clôture).

### Transactions
- **Clôture** : une transaction atomique (statut, date, effectif et poids finaux, observation, lot d'origine `CLOTUREE`). Elle n'écrit **aucun mouvement**.
- **Évacuation** : une transaction distincte, demandée juste après la clôture ou plus tard. Elle crée une `SORTIE` de l'effectif final, **datée du jour de la clôture**, liée au lot de grossissement (opération source), et renseigne `evacue_le` / `evacue_par`. Un lot dont l'effectif final est nul n'a rien à évacuer.

### Un lot clôturé ne reçoit plus d'enregistrement
- Pas de pesée, pas de modification du lot, pas de seconde clôture, pas de réouverture.
- **Registre figé** (bassin + espèce identifient le lot) : tout nouveau mouvement daté de la période du lot (de la mise en place à la clôture) est refusé, ainsi que tout mouvement postérieur **tant que le bassin n'est pas évacué**. Seule l'évacuation du lot passe ; après elle, un nouveau lot peut entrer dès le jour de la clôture.
- Un nouveau lot de **même espèce** est refusé dans le bassin tant que le lot clôturé n'est pas évacué. Une autre espèce du même bassin n'est pas concernée.
- L'annulation des mouvements de la période du lot reste refusée (règle existante).
- **Provende figée** (pour protéger les indicateurs, dont l'ICA) : en bassin de grossissement, une distribution, ou son annulation, datée de la période d'un lot clôturé est refusée ; une distribution est aussi refusée si aucun lot `ACTIF` n'est en place à sa date (bassin clôturé non évacué). Un lot d'une autre espèce encore en cours dans le bassin reçoit la provende dès le lendemain de la clôture. Les bassins de traitement et de stock ne sont pas concernés.

## 3. Historique des clôtures

Liste des lots clôturés, du plus récent au plus ancien :

| Colonne | Source |
|---|---|
| lot (`code`), espèce, bassin | lot de grossissement |
| date de mise en place, date de clôture, durée du cycle (jours) | lot |
| effectif initial, effectif final, taux de survie | final ÷ initial × 100 |
| poids moyen initial, poids moyen final | P0 et dernière pesée |
| écart de poids (%) | (final − initial) ÷ initial × 100, ex. 20 g → 300 g : +1 400 % |
| évacué, date d'évacuation | `evacue_le` |

Le **détail** d'une ligne (paramètres biologiques : séries, ICA, GMQ, biomasse finale) est le suivi du lot en lecture seule : `GET lots-grossissement/{id}/suivi/`.

## 4. Contrats de l'API

Préfixe : `/api/v1/grossissement/lots-grossissement/`. Lecture : Administrateur, Gestionnaire, Employé. Écriture (clôture, évacuation) : Administrateur et Gestionnaire. Non connecté : 401 ; employé en écriture : 403.

### `GET {id}/apercu-cloture/` (formulaire en lecture seule, rien n'est écrit)
```json
{
  "id_lot_grossissement": 7,
  "code": "GR-2026-0007",
  "espece": "CARPE",
  "statut": "ACTIF",
  "date_cloture": "2026-06-01",
  "effectif_initial": 1000,
  "effectif_final": 950,
  "poids_moyen_initial": 20.0,
  "poids_moyen_final": 300.0,
  "date_derniere_pesee": "2026-06-01",
  "seuil_poids_vente_g": 300,
  "ecart_poids_pourcentage": 1400.0,
  "cloturable": true,
  "raison_refus": null
}
```
Si la clôture est impossible : `cloturable: false` et `raison_refus` contient un message prêt à afficher (lot non actif, aucune pesée, poids inférieur au seuil).

### `POST {id}/cloturer/`
Corps : `{}` ou `{"observation": "..."}`. Tout autre champ (`motif`, `poids_final_g`, `date_cloture`…) est refusé (400). Réponse 200 : le lot (`statut: "CLOTURE"`, `date_cloture`, `effectif_final`, `poids_moyen_final`, `evacuation_requise`, `evacue_le`…) et `message`. Refus : 400 sur `statut` (lot non actif) ou `poids_moyen_final` (aucune pesée, seuil non atteint).

### `POST {id}/evacuer/`
Corps vide. Réponse 200 : le lot (`evacue_le` renseigné, `evacuation_requise: false`) et `message` (« Le bassin du lot … a bien été évacué. »). Refus : 400 sur `statut` (lot non clôturé) ou `detail` (déjà évacué, aucun poisson à évacuer).

### `GET historique-clotures/`
Filtres : `espece`, `bassin`, `date_min`, `date_max` (sur la date de clôture, `YYYY-MM-DD`). Réponse : une liste ; décimaux en chaînes comme le reste de l'API de liste.
```json
[
  {
    "id": 7,
    "code": "GR-2026-0007",
    "espece": "CARPE",
    "id_bassin": 3,
    "reference_bassin": "BG-03",
    "date_entree": "2026-01-01",
    "date_cloture": "2026-06-01",
    "duree_cycle_jours": 151,
    "effectif_initial": 1000,
    "effectif_final": 950,
    "taux_survie": "95.00",
    "poids_moyen_initial": "20.00",
    "poids_moyen_final": "300.00",
    "ecart_poids_pourcentage": "1400.00",
    "evacue": true,
    "evacue_le": "2026-06-01T10:15:00+03:00"
  }
]
```

### Suivi du lot (rappel des effets)
- `seuil.poids_vente_g` vaut le seuil de l'espèce ; `seuil.atteint`, `seuil.fin_cycle_possible` et l'alerte `POIDS_VENTE_ATTEINT` portent sur la **dernière pesée** (ce qui permet de clôturer). `date_premiere_pesee_au_seuil` reste un repère historique.
- `lot.evacuation_requise` signale un lot clôturé non évacué.

## 5. Données existantes

Migration `grossissement` 0009 : `poids_final_g` est renommé `poids_moyen_final` (valeurs conservées), le motif n'est plus obligatoire, et les lots déjà clôturés par l'ancien flux (qui évacuait toujours) sont marqués évacués à leur date de clôture.

## 6. Points ouverts

1. Seuil de la carpe (300 g) à faire valider par l'exploitant.
2. Âge maximal de la dernière pesée pour clôturer (aujourd'hui aucun : l'exploitant est invité à faire une pesée finale).
3. Évacuation partielle ou vers une vente : hors périmètre (la vente n'est pas gérée).
