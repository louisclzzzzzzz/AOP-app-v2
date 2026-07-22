# Architecture technique — AOP

Vue d'ensemble technique de l'application, pour quelqu'un qui code mais n'a pas (encore) lu le code. Volontairement synthétique — le détail exhaustif est dans `plan/PLAN.md` (spec) et directement dans le code (`backend/app/`).

## Stack

- **Backend** : Python 3.12 / FastAPI, servi via `uv`. SQLAlchemy + SQLite pour l'état. Pas de queue externe (Celery/Redis) : les pipelines tournent en tâches asyncio en mémoire, suffisant pour un usage local mono-utilisateur.
- **Frontend** : React + Vite + TypeScript + Tailwind, buildé en statique et servi directement par FastAPI (`StaticFiles` sur `frontend/dist`) — un seul process, un seul port.
- **Temps réel** : WebSocket par dossier (`app/api/websocket.py` + `progress.py`) pour la progression live (upload, OCR, classification, etc.).
- **IA** : uniquement Mistral, via un wrapper SDK maison (`app/mistral/client.py`) — retry, upload, OCR, chat structuré.
- **Déploiement** : Dockerfile multi-stage (build frontend Node → runtime `uv`+Python) + `fly.toml`, pour un déploiement de test sur Fly.io. `start.sh` reste le chemin local en une commande.

## Pipeline : machine à états par dossier

Chaque dossier (`Dossier` en base) traverse une séquence de statuts stricte (`store/models.py::DossierStatus`), affichée telle quelle côté frontend (`statusFlow.ts`) :

```
uploaded → unzipping → inventorying → extracting_text → ready_step1
  → classifying → classified [CHECKPOINT 1]
  → reorganizing → reorganized
  → analyzing_completeness → completeness_review [CHECKPOINT 2] → completeness_validated
  → extracting → extraction_review [CHECKPOINT 3] → extraction_validated
```

Les 3 pipelines IA (classification / complétude / extraction) partagent un bracket commun factorisé dans `pipeline_support.py` (`start_stage` / `finalize_stage` : passage de statut + broadcast WebSocket + recalcul des compteurs). Le corps métier de chaque pipeline (`classify/pipeline.py`, `completeness/pipeline.py`, `extraction/pipeline.py`) reste volontairement non factorisé — la logique diffère trop d'une étape à l'autre pour qu'une abstraction commune vaille le coup.

Chaque étape est **rejouable indépendamment** (endpoints `reopen` dédiés) sans tout refaire depuis le début, grâce au cache OCR et à l'état persisté en base.

## Modules backend (`backend/app/`)

| Module | Rôle |
| --- | --- |
| `ingestion/` | Dézip récursif (zips imbriqués, zip-slip protection, cp850), inventaire (SHA256, dédup), routage extraction de texte (natif vs OCR selon densité) |
| `ocr/` | Appel Mistral OCR + cache persistant par hash de contenu (`workspace/cache/text/`) |
| `classify/` | Étape 1 — moteur 3 signaux (regex filename, regex contenu, LLM Structured Outputs contraint à `config/taxonomy.yaml`), renommage, génération de la copie triée (`reorg.py`) |
| `completeness/` | Étape 2 — moteur de correspondance en 3 couches (`MatchLayer` : fichier direct → mots-clés intra-document → confirmation LLM) contre `config/pieces_checklist.yaml` |
| `extraction/` | Étape 3 — extraction structurée par champ, citation obligatoire, recoupement multi-documents sur les champs critiques, synthèse IA finale |
| `mistral/` | Wrapper bas niveau du SDK `mistralai` : retry, timeouts, upload, OCR, `chat.parse` (Structured Outputs) |
| `store/` | Modèles SQLAlchemy (dossier, document, classification, complétude, extraction), repository, session |
| `api/` | Routes REST par domaine + WebSocket |

## Décisions IA notables (`config/models.yaml`)

- **Un modèle par tâche, pas un modèle unique** : `mistral-large-2512` pour le raisonnement (complétude, extraction), `mistral-small-2603` pour la classification batchée (tâche facile, moins chère, n'entame pas le quota du modèle flagship), `mistral-medium-2604` pour les documents à forte composante image, `mistral-ocr-2512` pour l'OCR. Versions **datées épinglées** (pas `-latest`) pour la reproductibilité — avec un `fallback_latest` documenté en cas de retrait du modèle par Mistral.
- **Structured Outputs partout** : les catégories/champs possibles sont injectés dans le schéma Pydantic généré dynamiquement (`Literal[...]`), donc le LLM ne peut pas répondre hors taxonomie/schéma — pas de parsing de texte libre à la sortie.
- **Deux files distinctes, cadencées séparément** : OCR (`max_concurrency: 3`) et LLM chat (mono-worker, `min_interval_seconds`) — évite qu'un rate-limit sur l'une bloque l'autre.
- **Température 0** partout : reproductibilité plutôt que créativité.
- **Recoupement obligatoire** sur les champs critiques (montants, dates, garanties) : 2 passes croisant RC/CCAP/CCTP, incohérences signalées plutôt que masquées.
- **Optimisation en cours (`OPTIMISATION.md`)** : `defer_ocr_to_extraction` (flag actif) — l'OCR n'est plus systématique dès l'ingestion, il n'est déclenché à la demande qu'à l'étape 3 pour les documents réellement concernés par l'extraction, via `ensure_document_ocr`. Change le profil coût/latence sans changer le contrat de précision (l'OCR a toujours lieu avant qu'une valeur soit extraite d'un document scanné).

## Stockage & traçabilité

- `workspace/<dossier_id>/source/` — copie immuable de l'upload, jamais modifiée.
- `workspace/<dossier_id>/organized/` — copie triée générée à l'étape 1, régénérable (wipe + rebuild à chaque apply).
- `workspace/cache/text/<hash[:2]>/<hash>.md` + `.ocr.json` — texte extrait et réponse OCR brute (confiance par bloc, bounding boxes), **indexés par hash de contenu** : un fichier identique n'est jamais retraité, même dans un autre dossier.
- `workspace/aop.db` (SQLite) — état complet : dossiers, documents, décisions de classification/complétude/extraction, chacune avec confiance + méthode + modèle/version + horodatage.
- Rien de tout ça n'est versionné (`.gitignore`) ; c'est recréé au fil de l'eau.

## Frontend

SPA simple, pas de routeur : `App.tsx` bascule entre liste des dossiers et `DossierProgress.tsx` (vue détail). Le détail s'abonne au WebSocket du dossier, affiche le journal live, et expose 3 onglets qui n'apparaissent que lorsque le dossier a atteint le statut correspondant (`isAtOrAfter` sur `DossierStatus`). Chaque onglet est un composant autonome qui fait ses propres appels REST (`ReorganizationPlan`, `CompletenessChecklist`, `ExtractionSheet`).

## Configuration versionnée (`backend/config/`)

Tout ce qui est amené à changer sans toucher au code vit en YAML : `taxonomy.yaml` (catégories étape 1), `pieces_checklist.yaml` (pièces étape 2), `extraction_schema.yaml` (champs étape 3), `models.yaml` (modèles, seuils, concurrence, feature flags). C'est ce qui permet d'ajuster un seuil de confiance ou d'ajouter une pièce sans toucher au moteur.
