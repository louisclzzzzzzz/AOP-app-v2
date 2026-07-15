# AOP v2

Application web locale d'aide à l'analyse de DCE (Dossiers de Consultation des Entreprises)
pour l'underwriting assurance construction. Voir `PLAN.md` pour la spécification complète.

**Principe directeur : la précision et la traçabilité priment toujours sur la vitesse et le
coût.** OCR systématique sur documents scannés, citation obligatoire, aucune valeur inventée.

## État du projet

Phase 1 (« Socle ») livrée : upload d'un ZIP, dézippage récursif (zips imbriqués gérés),
inventaire complet, extraction de texte (natif + OCR Mistral avec cache persistant),
suivi de progression live par WebSocket, UI d'upload et de suivi.

Phase 2 (« Étape 1 — Réorganisation & renommage ») livrée : classification automatique de
chaque document par 3 signaux combinés (nom de fichier, contenu OCR, LLM `mistral-large`
en sortie structurée contrainte à la taxonomie — jamais une catégorie inventée), plan de
réorganisation éditable dans l'UI (catégorie, lot, nom cible), correction manuelle au
checkpoint humain, puis application d'une copie triée dans `workspace/<id>/organized/`
(la source n'est jamais modifiée) avec rapport JSON + Markdown. Validé contre le golden-set
de `arborescence.md` (signal nom de fichier) et par un run réel de bout en bout via l'API
Mistral (OCR + classification LLM).

Les étapes 2 (complétude) et 3 (extraction) — cf. `PLAN.md` §5-6 — seront livrées dans les
phases suivantes.

## Prérequis

- Python 3.11+ et [uv](https://docs.astral.sh/uv/)
- Node.js 20+
- Une clé API Mistral (https://console.mistral.ai/)
- (Optionnel, pour la conversion des fichiers `.doc` legacy) [LibreOffice](https://www.libreoffice.org/)
  installé et accessible via `soffice` dans le PATH. Sans lui, les `.doc` sont marqués en
  erreur explicite plutôt que d'inventer un texte non fiable — installez LibreOffice ou
  convertissez le fichier en `.docx`/`.pdf`.

## Installation

```bash
cp .env.example .env
# éditez .env et renseignez MISTRAL_API_KEY
```

## Lancement (une commande)

```bash
./start.sh
```

Build le frontend, installe les dépendances backend, puis sert l'application complète
(API + WebSocket + frontend) sur **http://localhost:8000**.

## Développement (hot-reload)

Deux terminaux :

```bash
# Terminal 1 — backend (auto-reload)
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000

# Terminal 2 — frontend (hot-reload, proxy vers le backend)
cd frontend
npm install
npm run dev
```

Ouvrez **http://localhost:5173** (le serveur Vite proxifie `/api` et `/ws` vers le port 8000,
voir `frontend/vite.config.ts`).

## Tests

```bash
cd backend
uv run pytest -v
```

Les tests ne nécessitent **aucune clé API réelle** : les appels Mistral sont simulés
(`monkeypatch`) pour les cas nécessitant de l'OCR ; le pipeline complet est validé de bout
en bout via l'API réelle sur des documents natifs (texte dense, aucun OCR déclenché).

## Configuration (`backend/config/`)

- **`models.yaml`** — modèles Mistral utilisés (versions épinglées), seuils de confiance,
  seuils de densité de texte pour le routage natif/OCR, flags de fonctionnalités
  (`precompute_rcmo_trc` désactivé par défaut, cf. PLAN §12). Les versions datées se
  périment côté API Mistral (modèles retirés) : si l'upload échoue en erreur
  `invalid_model`, mettez à jour `ocr.model` / `llm.model` avec une version listée par
  `client.models.list()`.
- **`taxonomy.yaml`** — taxonomie de classement de l'étape 1 (catégories, mots-clés
  filename/contenu, lot-awareness), dérivée de `PLAN.md` §4.2 et du golden-set.
- `pieces_checklist.yaml`, `extraction_schema.yaml` — à venir avec les étapes 2 et 3.

Toute évolution de version de modèle ou de seuil se fait dans ces fichiers, jamais en dur
dans le code.

## Architecture

```
backend/
├── app/
│   ├── main.py            # FastAPI + WebSocket + montage du frontend buildé
│   ├── api/                # routes REST + WebSocket
│   ├── ingestion/          # dézip récursif, inventaire, routage extraction de texte
│   ├── ocr/                 # appel Mistral OCR haut niveau + cache persistant
│   ├── classify/            # étape 1 : taxonomie, moteur 3 signaux, renommage, copie triée
│   ├── mistral/             # wrapper SDK bas niveau (retry, upload, OCR, chat structuré)
│   ├── store/               # modèles SQLAlchemy, session, repository
│   └── settings.py          # config .env + config/*.yaml
├── config/{models,taxonomy}.yaml
└── tests/
frontend/                    # React + Vite + TypeScript + Tailwind
workspace/                   # dossiers en cours (source immuable / organized / cache / DB)
                              # — jamais versionné, recréé au fil de l'eau
start.sh                     # lancement en une commande
```

### AOP_WORKSPACE_DIR : toujours un chemin absolu si personnalisé

`start.sh` lance le serveur depuis `backend/`. Si vous personnalisez `AOP_WORKSPACE_DIR`
dans `.env`, utilisez un chemin **absolu** — une valeur relative comme `./workspace`
pointerait alors vers `backend/workspace/` (non couvert par `.gitignore`) plutôt que vers
la racine du dépôt. Par défaut (variable non définie), le code résout déjà
`<racine_du_dépôt>/workspace` en absolu, indépendamment du répertoire de lancement.

### Traçabilité et cache OCR

- `workspace/<dossier_id>/source/` : copie immuable de ce qui a été déposé (jamais modifiée).
- `workspace/cache/text/<hash[:2]>/<hash>.md` : texte extrait, mis en cache par **hash de
  contenu** — un document identique (même octets), même dans un autre dossier, n'est jamais
  ré-extrait ni ré-OCRisé.
- `workspace/cache/text/<hash[:2]>/<hash>.ocr.json` : réponse OCR brute (confiance par page,
  bounding boxes) conservée pour une citation précise dans les étapes suivantes.
- `workspace/aop.db` (SQLite) : état des dossiers, inventaire, cache, classification — toute
  décision porte confiance, méthode, modèle+version et horodatage.
- `workspace/<dossier_id>/organized/` : copie triée générée à l'étape 1 (jamais la source).
- `workspace/<dossier_id>/organized_report.{json,md}` : rapport source → cible, confiance,
  justification, pour chaque fichier copié.
