# Fish Farm : page de suivi de cycle (contrat JSON et normes de la page)

Décrit la **page de suivi de cycle** d'un lot de grossissement, le contrat de l'API qui l'alimente (implémenté, phase 4) et les normes d'affichage. Les règles métier sont dans `REGLES_DE_GESTION_FISH_FARM.md` et `CONTEXTE_INDICATEURS_GROSSISSEMENT.md` ; les routes et les droits dans `GUIDE_INTEGRATION_FRONTEND.md`.

**Source de vérité du contrat** : les fichiers `docs/contrats/*.exemple.json`, produits par l'API réelle et contrôlés par un test (`apps/grossissement/tests/test_suivi.py`). Les extraits ci-dessous les résument ; en cas de doute, ce sont les fichiers qui font foi.

## 0. Principes

- La page **lit** ; elle ne calcule rien (ni somme, ni division, ni arrondi). Le backend lit les instantanés `IndicateurLot` sans les recalculer.
- Noms et valeurs **du code** : statut `ACTIF` / `CLOTURE`, `code` du lot, espèce `CARPE` / `TILAPIA` (le libellé est à faire côté frontend), `id_bassin` + `reference_bassin`, mesures environnementales sous leurs noms de modèle.
- Poids moyen **mesuré** sur échantillon, biomasse **estimée**. Aucune interpolation entre deux pesées.
- Libellé visible « ICA » ; les champs gardent `ic_periode` et `ic_cumule`.
- Lecture permise à l'Administrateur, au Gestionnaire et à l'Employé ; non connecté : 401.

## 1. Scénario utilisateur

1. L'utilisateur **sélectionne un lot de grossissement en cours** (liste, section 3).
2. Il ouvre la section **Paramètres biologiques**, qui affiche de haut en bas :
   1. **Tableau de bord en chiffres** : poids moyen, GMQ, biomasse, ICA cumulé.
   2. **Courbes** alignées : poids moyen, biomasse, GMQ, ICA.
   3. **Zone réservée** : courbe de Gauss d'homogénéité et sexage (à venir, sans appel d'API).
   4. **Zone réservée** : interactions avec le service IA (à venir, sans appel d'API).
3. Depuis la page : lien **Pesée** (formulaire de pesée), lien **Clôturer le lot** (si `seuil.fin_cycle_possible`), et pour un lot clôturé non évacué, l'action **Évacuer le bassin** (voir `CONTEXTE_CLOTURE_LOT.md`).

## 2. Périmètre

Backend : fait (suivi, liste, exemples). Frontend : sections 1 et 2 de la page, branchées d'abord sur les fichiers d'exemple puis sur l'API, sans changement d'affichage.

## 3. Sélection du lot

`GET /api/v1/grossissement/lots-grossissement/?statut=ACTIF` (filtres possibles : `espece`, `bassin`, `date_min`, `date_max`). Liste non paginée. Champs utiles à la sélection : `id`, `code`, `espece`, `id_bassin`, `reference_bassin`, `statut`, `date_entree`, `jour_de_cycle` (plus, entre autres, `poids_moyen_actuel`, `date_derniere_pesee`, `prochaine_echeance_pesee`, `pesee_en_retard`, `evacuation_requise`). Exemple complet : `docs/contrats/lots_en_cours.exemple.json`. Attention : dans cette liste, les décimaux sont des **chaînes** (`"40.00"`), comme dans toutes les ressources de l'API ; seuls les contrats de suivi et d'aperçu renvoient des nombres.

Un lot clôturé n'apparaît pas avec `?statut=ACTIF` mais reste consultable par son identifiant (et depuis l'historique des clôtures).

## 4. Contrat du suivi

`GET /api/v1/grossissement/lots-grossissement/{id}/suivi/?date_reference=AAAA-MM-JJ` (`date_reference` facultatif, défaut : aujourd'hui, fuseau Indian/Antananarivo ; outil de test, non exposé à l'utilisateur final).

### Règles du format
- Dates ISO (`AAAA-MM-JJ`) ; dates-heures avec décalage (`+03:00`).
- Décimaux en **nombres JSON**, à la précision enregistrée (poids 2 décimales, biomasse 3, GMQ 2, ICA 2, provende 3).
- Valeur non calculable : `null`, jamais 0. Listes vides : `[]`. **Toutes les clés sont toujours présentes.**
- Tout est vu à `date_reference` (pesées, effectif, environnement, calendrier, alertes). Pour un lot clôturé, le jour de cycle et le calendrier s'arrêtent à la date de clôture, sans prochaine pesée ni alerte.

### Structure (extrait de `docs/contrats/suivi_lot.exemple.json`, lot CARPE au 2026-03-10)
```json
{
  "lot": {
    "id": 1, "code": "GR-2026-0001", "espece": "CARPE",
    "id_bassin": 2, "reference_bassin": "GROSS-DEST", "statut": "ACTIF",
    "date_entree": "2026-01-01", "jour_de_cycle": 68,
    "effectif_initial": 1000, "poids_moyen_initial": 20.0,
    "effectif_vivant_actuel": 950, "evacuation_requise": false
  },
  "nombre_pesees": 2,
  "dernieres_valeurs": {
    "date_pesee": "2026-03-02", "jour_de_cycle": 60, "effectif_vivant": 950,
    "poids_moyen": 80.0, "biomasse_kg": 76.0,
    "gmq_periode": 1.17, "gmq_cumule": 1.0, "ic_periode": 2.82, "ic_cumule": 2.5,
    "provende_periode_kg": 90.0,
    "nature": { "poids_moyen": "mesuré sur échantillon", "biomasse_kg": "estimée" }
  },
  "point_initial": { "date": "2026-01-01", "jour_de_cycle": 0, "poids_moyen": 20.0, "biomasse_kg": 20.0 },
  "series": [
    { "pesee_id": 1, "date": "2026-01-31", "jour_de_cycle": 30, "poids_moyen": 45.0,
      "effectif_vivant": 980, "gmq_periode": 0.83, "gmq_cumule": 0.83,
      "ic_periode": 2.07, "ic_cumule": 2.07, "biomasse_kg": 44.1, "provende_periode_kg": 50.0 }
  ],
  "calendrier": {
    "prochaine_pesee": { "numero": 3, "jour_cible": 75, "date_prevue": "2026-03-17",
                         "jours_restants": 7, "en_retard": false, "jours_de_retard": 0 },
    "pesees_prevues": [ { "numero": 1, "jour_cible": 30, "date_prevue": "2026-01-31" },
                        { "numero": 2, "jour_cible": 60, "date_prevue": "2026-03-02" } ]
  },
  "seuil": { "poids_vente_g": 300, "atteint": false, "fin_cycle_possible": false,
             "date_premiere_pesee_au_seuil": null },
  "alertes": [],
  "parametres_environnementaux": {
    "date_heure_prelevement": "2026-03-09T08:00:00+03:00",
    "temperature_eau": 27.5, "temperature_air": 29.0, "oxygene": 6.1,
    "ph": 7.2, "nitrite": 0.05, "nitrate": 1.2, "ammoniac": 0.02
  },
  "autres_lots_du_bassin": []
}
```
- `series` : une entrée par pesée **valide** datée au plus tard à `date_reference`, en ordre chronologique. `effectif_vivant` est relu dans le registre.
- `dernieres_valeurs` : `null` s'il n'y a aucune pesée (voir `docs/contrats/suivi_lot_vide.exemple.json`).
- `point_initial` : J0 ; poids et biomasse `null` pour un lot sans pesée initiale.
- `calendrier.prochaine_pesee` : première échéance (J30, J60, puis tous les 15 jours) strictement postérieure à la dernière pesée ; `null` pour un lot clôturé. `pesees_prevues` : échéances datées au plus tard à `date_reference`.
- `seuil` : `poids_vente_g` = seuil de l'espèce (TILAPIA 250, CARPE 300) ; `atteint` et `fin_cycle_possible` portent sur la **dernière** pesée ; `date_premiere_pesee_au_seuil` est un repère historique.
- `parametres_environnementaux` : dernier prélèvement du bassin au plus tard à `date_reference`, ou `null`. Unités non enregistrées en base (°C, mg/L… à afficher par le frontend).
- `autres_lots_du_bassin` : autres lots `ACTIF` du bassin (`id`, `code`, `espece`).

### Alertes (calculées à la volée, lot `ACTIF` seulement)
```json
[
  { "code": "PESEE_EN_RETARD", "niveau": "attention",
    "message": "La pesée prévue le 2026-03-17 n'a pas été saisie.",
    "date_prevue": "2026-03-17", "jours_de_retard": 8 },
  { "code": "POIDS_VENTE_ATTEINT", "niveau": "info",
    "message": "Le poids moyen a atteint 320 g, au-delà du seuil de 300 g : fin de cycle possible.",
    "poids_moyen": 320.0, "seuil_g": 300, "date_pesee": "2026-04-10" }
]
```

### Erreurs
Lot inexistant : 404. `date_reference` invalide : 400 `{"date_reference": "Format invalide. Utilisez YYYY-MM-DD."}`. Non connecté : 401. Utilisateur inactif : 403.

## 5. Correspondance entre la page et le contrat

### Tableau de bord en chiffres (source : `dernieres_valeurs`)
| Carte | Valeur principale | Information secondaire | Unité | Nature |
|---|---|---|---|---|
| Poids moyen | `poids_moyen` | `date_pesee`, jour de cycle | g | mesuré sur échantillon |
| GMQ | `gmq_cumule` | `gmq_periode` | g/j par poisson | calculé |
| Biomasse | `biomasse_kg` | `effectif_vivant` à la pesée | kg | **estimée** |
| ICA cumulé | `ic_cumule` | `ic_periode` | kg d'aliment par kg de gain | calculé |

Autres éléments d'en-tête, sans calcul : code du lot, espèce, bassin, jour de cycle, effectif vivant actuel, prochaine pesée prévue (avec le retard), seuil de l'espèce et signal « fin de cycle possible », dernières mesures environnementales avec leur date, alertes en bandeau.

### Courbes (source : `point_initial` et `series`)
| Courbe | Type | Séries | Départ |
|---|---|---|---|
| Poids moyen | ligne avec points | `poids_moyen` ; ligne de référence au seuil (`seuil.poids_vente_g`) | `point_initial` (J0) |
| Biomasse | ligne avec points (aire légère possible) | `biomasse_kg`, marqueur « estimée » | `point_initial` (J0) |
| GMQ | barres `gmq_periode` + ligne `gmq_cumule` | une barre par pesée, à la date de fin de période | pas de J0 |
| ICA | barres `ic_periode` + ligne `ic_cumule` | idem | pas de J0 |

## 6. Normes de la page

### Données
- Aucune formule dans le frontend. Un seul appel de suivi par lot affiché ; rechargement après une action qui modifie les données (pesée, correction, annulation, clôture, évacuation) ; pas de rafraîchissement automatique.

### Valeurs absentes
- `null` s'affiche « — » avec une explication au survol (« non calculable : gain de biomasse nul ou provende non enregistrée », selon le cas). Jamais 0.
- Dans les courbes, `null` crée un **trou** (pas de point, pas de barre, pas de segment).

### Courbes
- Bibliothèque déjà présente dans le template, sans nouvelle dépendance (signaler s'il manque les barres + ligne combinées).
- Abscisse : **jour de cycle**, **proportionnelle au temps** (dates de pesée libres, intervalles irréguliers) ; date dans l'infobulle.
- Pas de double axe vertical ; infobulle : date, jour de cycle, valeur avec unité, nature ; dernier point mis en évidence.
- Pas de zone « bon / mauvais » sur l'ICA tant qu'aucun seuil n'est fixé.
- Couleurs du thème, lisibles en clair et en sombre, information jamais portée par la couleur seule.
- Quatre courbes en ligne sur grand écran, deux par ligne sur écran moyen, une par ligne sur téléphone.

### États de la page
- **Chargement** : squelette des cartes et des courbes.
- **Aucune pesée** (`nombre_pesees = 0`) : cartes à « — », courbes réduites au point initial, message « Aucune pesée enregistrée. Première pesée prévue le … ».
- **Lot clôturé** : bandeau « Cycle clôturé » ; pas d'alerte ni de prochaine pesée ; tout reste consultable ; si `lot.evacuation_requise`, rappel « Bassin non évacué » avec l'action d'évacuation (Administrateur, Gestionnaire).
- **Erreur** : 404, accès refusé, erreur réseau, avec un message clair et un bouton « Réessayer ».
- **Alertes** : bandeau en tête ; `attention` (retard) visuellement distinct de `info` (poids de vente atteint).
- **Autre lot dans le bassin** : mention discrète avec un lien vers ce lot ; pas de superposition de deux lots dans les mêmes courbes.

### Présentation
- Français, nombres au format français (virgule décimale, séparateur de milliers), unités visibles, mot « ICA ».
- Dates en format local lisible, fuseau Indian/Antananarivo.
- Accessibilité : contrastes, alternatives textuelles des courbes, navigation au clavier.
- La page est en lecture seule ; la saisie de pesée, la clôture et l'évacuation sont des actions séparées soumises aux droits.

## 7. Zones réservées

- **Homogénéité des poids et sexage** : à venir. La pesée n'enregistre que le nombre pesé et le poids total ; une courbe de Gauss demandera les poids individuels (saisie supplémentaire à prévoir).
- **Interactions LLM** : à venir, par un endpoint dédié du service IA ; aucune clé réservée dans ce contrat.

## 8. Critères de recette

- La liste des lots en cours permet de choisir un lot ; la page se charge depuis l'exemple JSON puis depuis l'API sans autre changement.
- Les quatre cartes et les quatre courbes affichent les valeurs du contrat, sans calcul.
- Valeurs nulles, absence de pesée, lot clôturé et erreurs sont gérés ; alertes selon leur niveau ; aucun appel pour les zones réservées.
- Correct à trois largeurs d'écran et en mode clair et sombre.

## 9. Points ouverts

1. Valeur principale des cartes GMQ et ICA (cumulé par défaut, période en secondaire).
2. Unités des paramètres environnementaux.
3. Comparaison de deux lots du même bassin dans un même graphique (écartée pour l'instant).
