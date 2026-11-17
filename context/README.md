# Documentation de contexte (backend Fish Farm)

État au 2026-11-17, aligné sur le code de la branche `fix/refont-sortie-grossissement`.

| Document | Pour qui | Contenu |
|---|---|---|
| `REGLES_DE_GESTION_FISH_FARM.md` | toute l'équipe, assistant de code | Règles métier de référence, module par module (registre, hapas, traitement, stock, mortalités, provende, sortie, grossissement), décisions et points ouverts. |
| `CONTEXTE_INDICATEURS_GROSSISSEMENT.md` | backend, frontend | Grossissement : modèle de données, sortie et création du lot, pesées, formules des indicateurs, répartition de la provende. |
| `CONTEXTE_CLOTURE_LOT.md` | backend, frontend | Clôture d'un lot de grossissement, évacuation, registre et provende figés, historique, contrats JSON. |
| `CONTEXTE_PHASE4_PAGE_SUIVI.md` | frontend | Page de suivi de cycle : contrat JSON, correspondance page ↔ contrat, normes d'affichage. |
| `GUIDE_INTEGRATION_FRONTEND.md` | frontend | Authentification, conventions de l'API, matrice des droits, parcours écran par écran, points d'attention. |
| `../docs/contrats/*.exemple.json` | frontend | Réponses réelles de l'API (suivi, liste des lots), contrôlées par les tests : données factices. |
| `../tests/postman/README_TEST.md` | backend, recette | Collections Postman et commandes newman. |
| `archives/` | historique | Prompts exécutés et anciens plans, **obsolètes** : ne pas s'en servir comme référence. |

Ordre de priorité en cas d'écart : le code et ses tests, puis `REGLES_DE_GESTION_FISH_FARM.md`, puis les autres contextes. Signaler tout écart plutôt que de le corriger en silence.
