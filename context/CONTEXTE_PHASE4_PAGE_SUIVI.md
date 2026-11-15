# Fish Farm : phase 4, suivi de cycle (contexte de la page et contrat JSON)

Ce document complète `REGLES_DE_GESTION_FISH_FARM.md` et `CONTEXTE_INDICATEURS_GROSSISSEMENT.md`, supposés déjà connus. Il ne répète pas les règles métier : il décrit la **page de suivi de cycle**, ce que le backend de la phase 4 doit fournir pour l'alimenter, et les normes à respecter. Les points marqués **[À CONFIRMER]** ne sont pas tranchés.

Il accompagne `PROMPT_PHASE4_SUIVI_CYCLE_v2.md` (spécification du backend). En cas d'écart entre les deux, signaler l'écart et poser la question avant de coder.

## 0. Continuité avec les décisions déjà prises

- La pesée est désormais **plate** : une `Pesee` = un lot, une date (refonte « une pesée = un lot »). Partout où la spécification de la phase 4 parle de « lignes de pesée » ou de `PeseeLot`, lire `Pesee`.
- Le suivi **lit** les instantanés `IndicateurLot` déjà calculés. Il ne recalcule rien. Le frontend **n'effectue aucun calcul** non plus : il affiche les valeurs reçues.
- Le calendrier de pesée sert uniquement à la prochaine pesée prévue et à l'alerte de retard ; il ne bloque jamais la saisie.
- Poids moyen **mesuré** sur échantillon, biomasse **estimée**. Aucune interpolation entre deux pesées.
- Libellé visible : **ICA** (indice de conversion alimentaire). Les champs de l'API gardent `ic_periode` et `ic_cumule`.
- Lecture du suivi permise à l'Administrateur, au Gestionnaire et à l'Employé ; utilisateur inactif ou non connecté refusé.

## 1. Scénario utilisateur de la page

1. Dans la page de suivi, l'utilisateur **sélectionne un lot de grossissement en cours**.
2. Il ouvre la section **Paramètres biologiques**.
3. La page affiche, de haut en bas :
   1. **Tableau de bord en chiffres** : poids moyen, GMQ, biomasse, ICA cumulé.
   2. **Courbes** alignées horizontalement : poids moyen, biomasse, GMQ, ICA.
   3. **Zone réservée** à la courbe de Gauss d'homogénéité des poids et au résultat du sexage (prochain sprint).
   4. **Zone réservée** aux interactions avec le LLM (service IA, phases ultérieures).

Pour cette phase, seules les sections 1 et 2 sont alimentées. Les sections 3 et 4 sont des **emplacements vides** (« à venir »), sans appel d'API.

## 2. Périmètre de cette phase

**Backend** : l'endpoint de suivi d'un lot (contrat de la section 4) et la liste des lots à sélectionner (section 3). **Frontend** : les sections 1 et 2 de la page, branchées d'abord sur l'exemple JSON puis sur l'API.

Hors périmètre : formulaire de pesée, clôture du lot, courbe de Gauss, sexage, appels au LLM, alertes autres que les deux retenues, notifications, export.

## 3. Sélection du lot (liste)

Réutiliser la liste existante des lots de grossissement avec un filtre `statut=EN_COURS`. Chaque élément expose au minimum : `id`, `reference`, `espece`, `bassin` (`id`, `reference`), `statut`, `date_entree`, `jour_de_cycle`. Si la liste existante n'expose pas ces champs ou le filtre, les ajouter sans casser ses consommateurs actuels et le signaler. Un lot clôturé reste consultable (lecture seule) si l'utilisateur y accède par son identifiant, mais n'apparaît pas dans la sélection par défaut.

Exemple :
```json
[
  {
    "id": 5,
    "reference": "LG-0005",
    "espece": "Tilapia",
    "bassin": { "id": 3, "reference": "BG-03" },
    "statut": "EN_COURS",
    "date_entree": "2026-01-01",
    "jour_de_cycle": 68
  }
]
```

## 4. Contrat JSON du suivi

`GET lots-grossissement/{id}/suivi/` (adapter la route aux conventions existantes). Paramètre facultatif `date_reference` (`YYYY-MM-DD`, défaut : date locale du jour) : il sert aux tests et à la consultation des données simulées « à une date donnée ».

### Règles du format
- Dates au format ISO (`YYYY-MM-DD`, ou date-heure avec décalage pour les mesures horodatées).
- Les valeurs décimales sont des **nombres JSON**, jamais des chaînes, arrondies à la précision enregistrée (poids moyen 2 décimales, biomasse 3, GMQ 2, ICA 2, provende 3).
- Une valeur non calculable est `null`, jamais 0. Les listes vides sont `[]`.
- Les messages d'alerte sont en français, prêts à être affichés.
- Les champs listés sont **tous toujours présents** (jamais d'absence de clé) ; seules leurs valeurs peuvent être `null`.

### Exemple : lot avec deux pesées (`date_reference=2026-03-10`)
```json
{
  "lot": {
    "id": 5,
    "reference": "LG-0005",
    "espece": "Tilapia",
    "bassin": { "id": 3, "reference": "BG-03" },
    "statut": "EN_COURS",
    "date_entree": "2026-01-01",
    "jour_de_cycle": 68,
    "effectif_initial": 1000,
    "poids_moyen_initial": 20.0,
    "effectif_vivant_actuel": 950
  },
  "nombre_pesees": 2,
  "dernieres_valeurs": {
    "date_pesee": "2026-03-02",
    "jour_de_cycle": 60,
    "effectif_vivant": 950,
    "poids_moyen": 80.0,
    "biomasse_kg": 76.0,
    "gmq_periode": 1.17,
    "gmq_cumule": 1.0,
    "ic_periode": 2.82,
    "ic_cumule": 2.5,
    "provende_periode_kg": 90.0,
    "nature": {
      "poids_moyen": "mesuré sur échantillon",
      "biomasse_kg": "estimée"
    }
  },
  "point_initial": {
    "date": "2026-01-01",
    "jour_de_cycle": 0,
    "poids_moyen": 20.0,
    "biomasse_kg": 20.0
  },
  "series": [
    {
      "pesee_id": 11,
      "date": "2026-01-31",
      "jour_de_cycle": 30,
      "poids_moyen": 45.0,
      "gmq_periode": 0.83,
      "gmq_cumule": 0.83,
      "ic_periode": 2.07,
      "ic_cumule": 2.07,
      "biomasse_kg": 44.1,
      "effectif_vivant": 980,
      "provende_periode_kg": 50.0
    },
    {
      "pesee_id": 12,
      "date": "2026-03-02",
      "jour_de_cycle": 60,
      "poids_moyen": 80.0,
      "gmq_periode": 1.17,
      "gmq_cumule": 1.0,
      "ic_periode": 2.82,
      "ic_cumule": 2.5,
      "biomasse_kg": 76.0,
      "effectif_vivant": 950,
      "provende_periode_kg": 90.0
    }
  ],
  "calendrier": {
    "prochaine_pesee": {
      "numero": 3,
      "jour_cible": 75,
      "date_prevue": "2026-03-17",
      "jours_restants": 7,
      "en_retard": false,
      "jours_de_retard": 0
    },
    "pesees_prevues": [
      { "numero": 1, "jour_cible": 30, "date_prevue": "2026-01-31" },
      { "numero": 2, "jour_cible": 60, "date_prevue": "2026-03-02" }
    ]
  },
  "seuil": {
    "poids_vente_g": 250,
    "atteint": false,
    "fin_cycle_possible": false,
    "date_premiere_pesee_au_seuil": null
  },
  "alertes": [],
  "parametres_environnementaux": {
    "date_heure": "2026-03-09T08:00:00+03:00",
    "temperature_eau": 27.5,
    "temperature_air": 29.0,
    "taux_oxygene": 6.1
  },
  "autres_lots_du_bassin": []
}
```

Les champs de `parametres_environnementaux` sont ceux du **modèle existant** (l'exemple en montre trois à titre indicatif) : ne pas en inventer.

### Exemple : lot sans pesée (état vide, `date_reference=2026-01-11`)
```json
{
  "lot": {
    "id": 6,
    "reference": "LG-0006",
    "espece": "Carpe",
    "bassin": { "id": 4, "reference": "BG-04" },
    "statut": "EN_COURS",
    "date_entree": "2026-01-01",
    "jour_de_cycle": 10,
    "effectif_initial": 500,
    "poids_moyen_initial": 40.0,
    "effectif_vivant_actuel": 500
  },
  "nombre_pesees": 0,
  "dernieres_valeurs": null,
  "point_initial": {
    "date": "2026-01-01",
    "jour_de_cycle": 0,
    "poids_moyen": 40.0,
    "biomasse_kg": 20.0
  },
  "series": [],
  "calendrier": {
    "prochaine_pesee": {
      "numero": 1,
      "jour_cible": 30,
      "date_prevue": "2026-01-31",
      "jours_restants": 20,
      "en_retard": false,
      "jours_de_retard": 0
    },
    "pesees_prevues": []
  },
  "seuil": {
    "poids_vente_g": 250,
    "atteint": false,
    "fin_cycle_possible": false,
    "date_premiere_pesee_au_seuil": null
  },
  "alertes": [],
  "parametres_environnementaux": null,
  "autres_lots_du_bassin": []
}
```

### Exemples d'alertes
Calculées à la volée, jamais stockées, uniquement pour un lot `EN_COURS`.
```json
[
  {
    "code": "PESEE_EN_RETARD",
    "niveau": "attention",
    "message": "La pesée prévue le 2026-01-31 n'a pas été saisie.",
    "date_prevue": "2026-01-31",
    "jours_de_retard": 5
  },
  {
    "code": "POIDS_VENTE_ATTEINT",
    "niveau": "info",
    "message": "Le poids moyen a atteint 260 g, au-delà du seuil de 250 g : fin de cycle possible.",
    "poids_moyen": 260.0,
    "seuil_g": 250,
    "date_pesee": "2026-01-31"
  }
]
```

### Erreurs
Reprendre le format d'erreur existant. Lot inexistant : 404. `date_reference` invalide : 400. Non connecté : 401. Utilisateur inactif : refusé.

### Fichier d'exemple pour le frontend
Les exemples ci-dessus sont enregistrés dans `docs/contrats/` (par exemple `suivi_lot.exemple.json`, `suivi_lot_vide.exemple.json`, `lots_en_cours.exemple.json`). Le frontend s'en sert comme **données factices** avant de se brancher sur l'API : le passage des données factices à l'API ne doit rien changer à l'affichage.

## 5. Correspondance entre la page et le contrat

### Tableau de bord en chiffres
Source : `dernieres_valeurs`. Chaque carte affiche la valeur, son unité, la **date de la pesée** d'origine et la nature de la valeur.

| Carte | Valeur principale | Information secondaire | Unité | Nature |
|---|---|---|---|---|
| Poids moyen | `poids_moyen` | `date_pesee`, jour de cycle | g | mesuré sur échantillon |
| GMQ | `gmq_cumule` | `gmq_periode` | g/j par poisson | calculé |
| Biomasse | `biomasse_kg` | `effectif_vivant` à la pesée | kg | **estimée** |
| ICA cumulé | `ic_cumule` | `ic_periode` | kg d'aliment par kg de gain | calculé |

**[À CONFIRMER]** : valeur principale du GMQ (cumulé retenu par défaut, la période en secondaire) et de l'ICA (cumulé, période en secondaire, d'après « ICA moyen cumulé »).

Autres éléments d'en-tête, sans calcul côté page : référence du lot, espèce, bassin, jour de cycle, effectif vivant actuel, prochaine pesée prévue (avec le retard si présent), le seuil de 250 g (rappel de la valeur, signal « fin de cycle possible »), dernières mesures environnementales du bassin avec leur date, et les alertes en bandeau.

### Courbes
Source : `point_initial` et `series`. Quatre graphiques alignés horizontalement.

| Courbe | Type | Séries | Départ |
|---|---|---|---|
| Poids moyen | ligne avec points | `poids_moyen`, point initial inclus ; ligne de référence horizontale au seuil de 250 g (`seuil.poids_vente_g`) | `point_initial` (J0) |
| Biomasse | ligne avec points (aire légère possible) | `biomasse_kg`, point initial inclus ; marqueur différent pour signaler « estimée » | `point_initial` (J0) |
| GMQ | **barres** pour `gmq_periode` + **ligne** pour `gmq_cumule` | une barre par pesée, rattachée à la **date de fin** de la période | pas de point J0 |
| ICA | **barres** pour `ic_periode` + **ligne** pour `ic_cumule` | idem | pas de point J0 |

## 6. Normes de la page

### Données
- Aucune formule dans le frontend : il n'additionne, ne divise ni ne ré-arrondit. Tout vient du contrat.
- Un seul appel de suivi par lot affiché. Rechargement après toute action qui modifie les données (pesée, correction), pas de rafraîchissement automatique.
- Le choix du lot et, le cas échéant, `date_reference` passent par l'API ; `date_reference` n'est pas exposé à l'utilisateur final (outil de test).

### Valeurs absentes
- Une valeur `null` s'affiche « — » avec une courte explication au survol (« non calculable : gain de biomasse nul ou provende non enregistrée », selon le cas). Jamais 0.
- Dans les courbes, une valeur `null` crée un **trou** : pas de point, pas de barre, pas de segment qui relie les points voisins.

### Courbes
- **Bibliothèque de courbes déjà présente dans le template** : l'utiliser, sans ajouter de dépendance. S'il manque un type de graphique indispensable (barres et ligne combinées), le signaler plutôt que d'ajouter une bibliothèque.
- Abscisse : **jour de cycle** (J0, J30…), avec la date dans l'infobulle. L'axe est **proportionnel au temps** : les dates de pesée étant libres, les intervalles sont irréguliers et ne doivent pas être affichés comme réguliers.
- Pas de double axe vertical.
- Infobulle : date, jour de cycle, valeur avec unité, et la nature (mesuré ou estimé) quand elle existe.
- Mise en évidence du dernier point.
- Pas de zone « bon / mauvais » sur l'ICA tant que l'exploitant n'a pas fixé de seuil.
- Couleurs issues du thème du template, lisibles en mode clair et sombre, sans que la couleur seule porte l'information (légende et marqueurs différents).
- Mise en page : quatre courbes en ligne sur grand écran, deux par ligne sur écran moyen, une par ligne sur téléphone. La courbe du poids moyen peut être mise en avant.

### États de la page
- **Chargement** : squelette des cartes et des courbes.
- **Aucune pesée** (`nombre_pesees = 0`) : cartes à « — », courbes du poids moyen et de la biomasse réduites au point initial avec un message du type « Aucune pesée enregistrée. Première pesée prévue le … ».
- **Lot clôturé** : bandeau « Cycle clôturé » ; pas d'alerte ni de prochaine pesée ; tout reste consultable.
- **Erreur** : lot introuvable (404), accès refusé, erreur réseau, avec un message clair et un bouton pour réessayer.
- **Alertes** : bandeau en tête, niveau « attention » (retard) visuellement distinct du niveau « info » (poids de vente atteint).
- **Autre lot dans le bassin** (`autres_lots_du_bassin`) : mention discrète avec un lien pour passer à ce lot. Pas de superposition de deux lots dans les mêmes courbes pour l'instant.

### Présentation
- Libellés en français, nombres au format français (virgule décimale, séparateur de milliers), unités toujours visibles. Le mot « ICA » dans les libellés.
- Dates affichées en format local lisible ; fuseau du projet (Indian/Antananarivo).
- Accessibilité : contrastes suffisants, alternatives textuelles pour les courbes, navigation au clavier sur les cartes et les onglets.
- Rôles : la page est en lecture seule pour tous. Le bouton de saisie de pesée relève d'un autre lot de travail.

## 7. Zones réservées

- **Homogénéité des poids (courbe de Gauss) et sexage** : emplacements « à venir », sans donnée. **Point de vigilance pour ce futur sprint** : l'échantillon de pesée enregistre aujourd'hui seulement le nombre de poissons pesés et le poids total ; une courbe de Gauss (écart-type, coefficient de variation) demande les **poids individuels** ou au moins une mesure de dispersion, qui ne sont pas collectés. Ce sprint devra donc prévoir une saisie supplémentaire. Ne rien modifier du modèle de pesée dans cette phase.
- **Interactions LLM** : emplacement « à venir ». Elles passeront plus tard par un endpoint dédié alimenté par le service IA (les explications et recommandations s'appuient sur les indicateurs et anomalies déjà calculés, voir `CONTEXTE_SERVICE_IA.md`). Aucune clé réservée dans le contrat de suivi.

## 8. Critères de recette de la phase

- La liste des lots en cours permet de choisir un lot, et la page se charge à partir de l'exemple JSON puis de l'API sans autre changement.
- Les quatre cartes et les quatre courbes affichent les valeurs du contrat, sans calcul côté page.
- Les valeurs nulles, l'état « aucune pesée », le lot clôturé et les erreurs sont gérés.
- Les alertes s'affichent selon leur niveau.
- Aucun appel pour les zones réservées.
- Le comportement est correct à trois largeurs d'écran (téléphone, tablette, ordinateur) et en mode clair et sombre.

## 9. Points ouverts

1. Valeur principale des cartes GMQ et ICA (cumulé par défaut).
2. Tolérance de retard de pesée (0 jour par défaut).
3. Liste des champs des paramètres environnementaux et unité du taux d'oxygène.
4. Comparaison de deux lots du même bassin dans un même graphique : écartée pour l'instant, à rouvrir si le besoin apparaît.
