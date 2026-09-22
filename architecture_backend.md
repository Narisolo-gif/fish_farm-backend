# Architecture backend — Django / Django REST Framework / PostgreSQL

## A. Analyse préalable (MCD + architecture frontend)

### Principe directeur

Le découpage backend reprend **la même logique de domaines que le frontend** (`features/production/<domaine>`), pour qu'un développeur retrouve la même carte mentale des deux côtés : *"tout ce qui concerne le traitement"* est un dossier `traitement/` en frontend, une **app Django** `traitement` en backend.

### Ce que le MCD impose comme découpage

En reprenant les mêmes trois catégories qu'à l'étape frontend :

**1. Modèles propres à un seul domaine** → une app Django par domaine :
- `Reproduction`, `Ponte` → app `reproduction`
- `Eclosion` → app `ecloserie`
- `Traitement`, `TraitementHapa`, `MortaliteTraitement`, `ConsommationTraitement` → app `traitement`
- `LotStockage`, `MortaliteStockage`, `VenteLot`, `ConsommationStockage` → app `stocks`
- `Grossissement`, `ParticipationGrossissement`, `Pesee`, `MortaliteGrossissement`, `ConsommationGrossissement` → app `grossissement`
- `Alerte`, `AnalyseIA`, `RapportPdf` → app `monitoring`

**2. Modèles transverses (référencés par plusieurs apps)** → apps Django dédiées, sans logique métier de domaine :
- `Bassin`, `Hapa` → app `bassins`
- `ParametreEnvironnemental`, `ReferentielMesure`, `MesureEnvironnementale` → app `environnement`
- `TypeProvende`, `Fournisseur`, `Provende`, `MouvementStockProvende` → app `provende`
- `Lot` → app `lots` (pivot central : né d'une `Eclosion`, référencé par `Traitement`, `LotStockage`, `ParticipationGrossissement`, `VenteLot`)
- `Role`, `Utilisateur` → app `core`

**3. Concept dupliqué en base, non mutualisable en modèle** → la mortalité et la consommation de provende restent **chacune un modèle par app** (`MortaliteTraitement`, `MortaliteStockage`, `MortaliteGrossissement` / `ConsommationTraitement`, `ConsommationStockage`, `ConsommationGrossissement`), exactement comme côté frontend où seule l'UI est mutualisée (`shared/mortalite-ui`, `shared/provende-ui`) et pas le service. Créer un modèle Django unique avec un `ContentType` générique pour la mortalité serait une abstraction prématurée : les trois tables ont des FK parentes différentes et pas de requête transversale identifiée dans le cahier des charges qui le justifierait.

### Règle de dépendance entre apps

Pour éviter les imports circulaires et garder le graphe de dépendances lisible :
- Les apps transverses (`core`, `bassins`, `environnement`, `provende`, `lots`) **ne dépendent d'aucune app de domaine**.
- Les apps de domaine (`reproduction`, `ecloserie`, `traitement`, `stocks`, `grossissement`, `monitoring`) peuvent dépendre des apps transverses, jamais l'inverse, et **ne dépendent pas les unes des autres** (ex. `traitement` ne doit pas importer de modèle de `grossissement`).
- Seule exception naturelle : `ecloserie` dépend de `reproduction` (`Eclosion.ponte` → FK vers `Ponte`), ce qui reflète la relation du MCD.

---

## B. Arborescence globale proposée

```
backend/
├── config/                          # projet Django (settings, urls racine)
│   ├── settings/                    # base.py / dev.py / prod.py (découpage par environnement)
│   └── urls.py                      # inclut les urls.py de chaque app sous /api/v1/...
│
├── apps/
│   │
│   ├── core/                                    # ── transverse ──
│   │   ├── models.py                            # Role, Utilisateur
│   │   ├── serializers.py
│   │   ├── views.py                             # auth, profil utilisateur
│   │   ├── permissions.py                       # permissions basées sur Role
│   │   └── urls.py
│   │
│   ├── bassins/                                 # ── transverse ──
│   │   ├── models.py                            # Bassin, Hapa
│   │   ├── serializers.py
│   │   ├── viewsets.py                          # CRUD bassin/hapa, config
│   │   ├── services.py                          # dispatch (répartition par hapa/bassin)
│   │   └── urls.py
│   │
│   ├── environnement/                           # ── transverse ──
│   │   ├── models.py                            # ParametreEnvironnemental, ReferentielMesure,
│   │   │                                          # MesureEnvironnementale
│   │   ├── serializers.py
│   │   ├── viewsets.py                          # mesures filtrables par bassin/hapa/date
│   │   └── urls.py
│   │
│   ├── provende/                                # ── transverse (catalogue) ──
│   │   ├── models.py                            # TypeProvende, Fournisseur, Provende,
│   │   │                                          # MouvementStockProvende
│   │   ├── serializers.py
│   │   ├── viewsets.py                          # catalogue + niveaux de stock
│   │   └── urls.py
│   │
│   ├── lots/                                    # ── transverse (pivot) ──
│   │   ├── models.py                            # Lot
│   │   ├── serializers.py
│   │   ├── viewsets.py                          # liste, détail
│   │   ├── services.py                          # historique transverse (agrège traitement,
│   │   │                                          # stockage, grossissement, vente pour un lot)
│   │   └── urls.py
│   │
│   ├── reproduction/                            # ── domaine ──
│   │   ├── models.py                            # Reproduction, Ponte
│   │   ├── serializers.py
│   │   ├── viewsets.py
│   │   └── urls.py
│   │
│   ├── ecloserie/                                # ── domaine ──
│   │   ├── models.py                            # Eclosion (FK -> reproduction.Ponte)
│   │   ├── serializers.py
│   │   ├── viewsets.py
│   │   └── urls.py
│   │
│   ├── traitement/                               # ── domaine ──
│   │   ├── models.py                            # Traitement, TraitementHapa,
│   │   │                                          # MortaliteTraitement, ConsommationTraitement
│   │   ├── serializers.py
│   │   ├── viewsets.py
│   │   ├── services.py                          # mortalité cumulée, taux sur période, survivants
│   │   └── urls.py
│   │
│   ├── stocks/                                   # ── domaine ──
│   │   ├── models.py                            # LotStockage, MortaliteStockage, VenteLot,
│   │   │                                          # ConsommationStockage
│   │   ├── serializers.py
│   │   ├── viewsets.py
│   │   ├── services.py                          # taux de survie, sortie (vente/grossissement)
│   │   └── urls.py
│   │
│   ├── grossissement/                            # ── domaine ──
│   │   ├── models.py                            # Grossissement, ParticipationGrossissement,
│   │   │                                          # Pesee, MortaliteGrossissement,
│   │   │                                          # ConsommationGrossissement
│   │   ├── serializers.py
│   │   ├── viewsets.py
│   │   ├── services.py                          # GMQ, ICA, biomasse, distribution des poids
│   │   └── urls.py
│   │
│   └── monitoring/                               # ── domaine (transverse fonctionnel) ──
│       ├── models.py                            # Alerte, AnalyseIA, RapportPdf
│       ├── serializers.py
│       ├── viewsets.py
│       └── urls.py
│
└── common/                                       # ── utilitaires génériques, non métier ──
    ├── pagination.py                             # classe de pagination DRF par défaut
    ├── exceptions.py                             # handler d'exceptions DRF uniforme
    └── mixins.py                                 # ex: TimestampedModel (created_at/updated_at)
```

---

## C. Rôle et contenu de chaque niveau

| Dossier / fichier | Rôle | Contient | Ne contient pas |
|---|---|---|---|
| `config/` | Configuration du projet Django | Réglages, montage des urls des apps | Modèles, logique métier |
| `apps/<app>/models.py` | Mapping ORM ↔ tables PostgreSQL | Classes `Model`, contraintes (`unique_together`, `CheckConstraint`), `Meta` | Sérialisation, logique de calcul |
| `apps/<app>/serializers.py` | Contrat de données API | `ModelSerializer`, validations de champs | Requêtes ORM complexes, règles métier |
| `apps/<app>/viewsets.py` (ou `views.py`) | Point d'entrée HTTP | `ModelViewSet`/`APIView`, filtrage, pagination | Calculs métier non triviaux (→ `services.py`) |
| `apps/<app>/services.py` | Logique métier / calculs | Fonctions pures ou classes de service appelées par les viewsets (ex. mortalité cumulée) | Code de sérialisation ou de routage |
| `apps/<app>/urls.py` | Routage de l'app | `router.register(...)` DRF | Vues elles-mêmes |
| `common/` | Utilitaires génériques à tout le projet | Pagination, gestion d'erreurs, mixins techniques | Toute logique liée à la pisciculture |

**Convention `services.py`** : ce fichier n'est créé que lorsque la logique dépasse le CRUD simple (ex. `traitement/services.py` pour les calculs de mortalité cumulée/taux/survivants équivalents à la vue SQL définie précédemment, `grossissement/services.py` pour GMQ/ICA/biomasse). Les apps purement CRUD (`core`, `provende`, `reproduction`, `ecloserie`) n'en ont pas besoin — l'ajouter "au cas où" irait à l'encontre de la contrainte de simplicité.

---

## D. Correspondance MCD → apps Django

| Entité(s) du MCD | App Django | Domaine frontend correspondant |
|---|---|---|
| `ROLE`, `UTILISATEUR` | `core` | (transverse, hors module Production) |
| `BASSIN`, `HAPA` | `bassins` | `shared/bassins` |
| `PARAMETRE_ENVIRONNEMENTAL`, `REFERENTIEL_MESURE`, `MESURE_ENVIRONNEMENTALE` | `environnement` | `shared/environnement` |
| `TYPE_PROVENDE`, `FOURNISSEUR`, `PROVENDE`, `MOUVEMENT_STOCK_PROVENDE` | `provende` | `shared/provende-ui` (catalogue) |
| `LOT` | `lots` | `shared/lot` |
| `REPRODUCTION`, `PONTE` | `reproduction` | `features/production/reproduction` |
| `ECLOSION` | `ecloserie` | `features/production/ecloserie` |
| `TRAITEMENT`, `TRAITEMENT_HAPA`, `MORTALITE_TRAITEMENT`, `CONSOMMATION_TRAITEMENT` | `traitement` | `features/production/traitement` |
| `LOT_STOCKAGE`, `MORTALITE_STOCKAGE`, `VENTE_LOT`, `CONSOMMATION_STOCKAGE` | `stocks` | `features/production/stocks` |
| `GROSSISSEMENT`, `PARTICIPATION_GROSSISSEMENT`, `PESEE`, `MORTALITE_GROSSISSEMENT`, `CONSOMMATION_GROSSISSEMENT` | `grossissement` | `features/production/grossissement` |
| `ALERTE`, `ANALYSE_IA`, `RAPPORT_PDF` | `monitoring` | `features/production/shared` (dashboards / notifications transverses) |

---

## E. Mutualisation

### À mutualiser (apps transverses)

- **`bassins`** : un seul modèle `Bassin`/`Hapa` en base ⇒ une seule app, appelée en `ForeignKey` par `environnement`, `reproduction`, `traitement`, `stocks`, `grossissement`.
- **`environnement`** : une seule table de mesures pour les cinq domaines ⇒ une seule app, filtrée par `bassin`/`hapa` en query params côté API.
- **`provende`** : le **catalogue** (types, fournisseurs, stock) est unique ⇒ une seule app exposant les endpoints de lecture/consultation utilisés par `traitement`, `stocks`, `grossissement`. Les **actions de consommation** restent dans l'app appelante (voir ci-dessous).
- **`lots`** : entité pivot, une seule app qui expose l'historique transverse (agrégé via `services.py` en interrogeant `traitement`, `stocks`, `grossissement`, `ecloserie` en lecture seule).
- **`common/`** : pagination, gestion d'erreurs DRF, mixins techniques (ex. horodatage) — zéro connaissance métier.

### À NE PAS mutualiser

- **Les modèles de mortalité** (`MortaliteTraitement`, `MortaliteStockage`, `MortaliteGrossissement`) : trois tables à FK parentes différentes, comme dans le MCD. Un modèle générique unique (via `ContentType`/`GenericForeignKey`) ajouterait une indirection sans bénéfice concret, alors qu'aucune requête transversale ("toute la mortalité, tous domaines confondus") n'est demandée dans le cahier des charges.
- **Les modèles de consommation** (`ConsommationTraitement`, `ConsommationStockage`, `ConsommationGrossissement`) : même raisonnement, chacun a ses propres champs (`ConsommationGrossissement` a un champ `etape` que les deux autres n'ont pas).
- **Les `services.py` métier** de chaque domaine (calculs GMQ/ICA/biomasse en grossissement, taux de survie en stocks, mortalité cumulée en traitement) : ce sont des règles de calcul propres à un domaine, pas des utilitaires génériques.

---

## F. Organisation des API

Chaque app expose son propre `urls.py` avec un `DefaultRouter` DRF, monté dans `config/urls.py` sous un préfixe correspondant au domaine :

```
/api/v1/auth/...                → core
/api/v1/bassins/...             → bassins (bassins, hapas)
/api/v1/environnement/mesures/  → environnement
/api/v1/provende/...            → provende (catalogue, mouvements de stock)
/api/v1/lots/...                → lots
/api/v1/reproduction/...        → reproduction (reproductions, pontes)
/api/v1/ecloserie/...           → ecloserie
/api/v1/traitement/...          → traitement (+ /mortalites, /consommations imbriqués)
/api/v1/stocks/...              → stocks (+ /mortalites, /consommations, /ventes imbriqués)
/api/v1/grossissement/...       → grossissement (+ /pesees, /mortalites, /consommations imbriqués)
/api/v1/monitoring/...          → monitoring (alertes, analyses, rapports)
```

Cette structure suit directement le mapping du tableau D : **un préfixe d'URL par app**, lui-même aligné sur un sous-module frontend — un développeur qui sait où trouver `features/production/traitement` sait aussi où trouver `/api/v1/traitement/`.

Les sous-ressources imbriquées (mortalités, consommations, pesées) restent des routes DRF **au sein de l'app propriétaire du parent** (ex. `traitement/urls.py` déclare aussi la route des mortalités de traitement), pour ne pas casser la règle de dépendance de la section A.

---

## G. Exemple concret détaillé — app `grossissement`

```
apps/grossissement/
├── models.py
│   ├── Grossissement                 (FK -> bassins.Bassin)
│   ├── ParticipationGrossissement    (FK -> Grossissement, FK -> lots.Lot)
│   ├── Pesee                         (FK -> Grossissement)
│   ├── MortaliteGrossissement        (FK -> Grossissement)
│   └── ConsommationGrossissement     (FK -> Grossissement, FK -> provende.Provende)
│
├── serializers.py
│   ├── GrossissementSerializer
│   ├── ParticipationGrossissementSerializer
│   ├── PeseeSerializer
│   ├── MortaliteGrossissementSerializer
│   └── ConsommationGrossissementSerializer
│
├── viewsets.py
│   ├── GrossissementViewSet          (CRUD + action `cloturer`)
│   ├── ParticipationGrossissementViewSet   ("dispatcher les lots par bassin")
│   ├── PeseeViewSet
│   ├── MortaliteGrossissementViewSet
│   └── ConsommationGrossissementViewSet
│
├── services.py
│   ├── calculer_gmq(grossissement)          # gain moyen quotidien à partir des Pesee
│   ├── calculer_biomasse_par_etape(...)
│   ├── calculer_ica_par_etape(...)           # nécessite Pesee + ConsommationGrossissement
│   └── calculer_distribution_poids(...)      # pour la courbe de Gauss côté frontend
│
└── urls.py    # /api/v1/grossissement/, /pesees/, /mortalites/, /consommations/

# Dépendances déclarées vers des apps transverses (jamais l'inverse) :
apps/bassins.Bassin          → sélection du bassin de destination
apps/lots.Lot                → lots intégrés au cycle
apps/provende.Provende       → provende consommée par étape
apps/environnement           → mesures du bassin de grossissement (lues, pas modifiées ici)
```

L'endpoint d'export Excel du dashboard (`ExportExcelButton` côté frontend) consomme les données déjà exposées par `GrossissementViewSet`/`services.py` — il ne nécessite pas d'app ni de endpoint dédié supplémentaire, juste un paramètre de format (`?format=xlsx`) sur les endpoints existants ou une action DRF `@action` dédiée sur le viewset, à confirmer selon le volume de données.

---

## H. Vérification finale

| Critère | Statut | Justification |
|---|---|---|
| Cohérence avec le MCD | ✅ | Chaque table a un propriétaire unique et clair : une app transverse pour les tables partagées, une app de domaine pour les tables propres |
| Cohérence avec l'architecture frontend | ✅ | Le découpage en apps Django reproduit exactement le découpage en `features/production/<domaine>` et `features/production/shared/*` |
| Modularité | ✅ | Chaque app est indépendante et suit la même structure interne (`models`, `serializers`, `viewsets`, `urls`, `services` si besoin) |
| Maintenabilité | ✅ | Séparation stricte modèle / sérialisation / vue / logique métier dans chaque app |
| Évolutivité | ✅ | Ajouter un sous-domaine = ajouter une app Django + son entrée dans `config/urls.py`, sans toucher aux apps existantes |
| Faible couplage | ✅ | Règle explicite de dépendance (transverse → jamais vers domaine ; domaine → jamais vers un autre domaine, sauf `ecloserie → reproduction` justifié par le MCD) |
| Simplicité / pas de sur-ingénierie | ✅ | Pas de modèle générique pour la mortalité/consommation ; `services.py` uniquement là où un calcul le justifie |
| Compatibilité Django + DRF + PostgreSQL | ✅ | Une app = un ensemble cohérent de modèles ORM mappés 1:1 aux tables PostgreSQL du script SQL fourni ; DRF routers standard, pas de couche d'abstraction supplémentaire |

---

## Ambiguïtés à lever avant implémentation

1. **Modèle utilisateur Django vs `UTILISATEUR`/`ROLE` du MCD.** Django fournit son propre système d'authentification (`django.contrib.auth.User`). Le MCD définit des tables `UTILISATEUR`/`ROLE` distinctes. Il faut décider : (a) remplacer entièrement le modèle utilisateur par un modèle personnalisé (`AUTH_USER_MODEL` pointant vers `core.Utilisateur`), ou (b) garder l'utilisateur Django standard et faire de `core.Utilisateur` un profil lié en `OneToOneField`. Ce choix a un impact structurant (migrations, permissions) et n'est pas tranché ici — je ne l'ai pas décidé arbitrairement.

2. **Création du `Lot` (rappel de la session précédente, toujours en attente).** Selon que la création du lot est automatique à l'éclosion ou une étape manuelle distincte (point à confirmer avec l'entreprise), l'endpoint de création vivra soit dans `ecloserie/viewsets.py` (déclenché à la création d'une `Eclosion`), soit dans `lots/viewsets.py` comme action indépendante. L'app `lots` existe dans les deux cas ; seul l'endroit où l'écriture se déclenche change.

3. **Export Excel des dashboards.** Le cahier des charges mentionne "extraction Excel des données du dashboard" pour Grossissement, mais ne précise pas s'il s'agit d'un export ponctuel (paramètre de format sur un endpoint existant) ou d'un besoin élargi à d'autres domaines/dashboards. Je n'ai pas créé d'app ou d'endpoint dédié à l'export tant que ce périmètre n'est pas confirmé.

4. **Historique transverse du `Lot`.** L'app `lots` doit agréger en lecture des données venant de `ecloserie`, `traitement`, `stocks`, `grossissement` pour reconstituer l'historique d'un lot. Cela suppose que `lots/services.py` interroge les modèles de ces apps directement (import autorisé dans le sens domaine ← transverse serait inversé ici). Cette lecture inter-app est nécessaire mais mérite d'être signalée : c'est la seule app transverse qui a besoin de connaître l'existence des apps de domaine pour fonctionner, ce qui déroge légèrement à la règle de dépendance stricte de la section A. Une alternative serait que chaque domaine expose son propre endpoint d'historique partiel et que le frontend les agrège — à arbitrer selon la préférence d'équipe.
