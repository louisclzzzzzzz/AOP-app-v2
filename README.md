# AOP v2

Application web locale d'aide à l'analyse de DCE (Dossiers de Consultation des Entreprises)
pour l'underwriting assurance construction. Voir `plan/PLAN.md` pour la spécification d'origine
et `docs/ARCHITECTURE.md` pour une description technique détaillée et à jour de tout le pipeline.

**Principe directeur : la précision et la traçabilité priment toujours sur la vitesse et le
coût.** OCR systématique sur documents scannés, citation obligatoire, aucune valeur inventée,
catégories/champs toujours contraints à un schéma (jamais de texte libre non validé).

## État du projet

Pipeline complet livré, du dépôt du ZIP au rapport d'audit final :

1. **Ingestion** — dézippage récursif (zips imbriqués gérés), inventaire, extraction de texte
   (natif + OCR Mistral avec cache persistant par hash de contenu), suivi de progression live
   par WebSocket.
2. **Étape 1 — Classification & réorganisation** — chaque document classé par 3 signaux combinés
   (nom de fichier, contenu OCR, LLM `mistral-large` en sortie structurée contrainte à la
   taxonomie), plan éditable dans l'UI, correction manuelle au checkpoint humain, puis copie
   triée dans `workspace/<id>/organized/` (la source n'est jamais modifiée).
3. **Étape 2 — Complétude** — vérification de la checklist de pièces attendues (moteur à 3
   couches : correspondance directe, recherche par mots-clés, vérification LLM groupée par
   document candidat), checkpoint humain de correction.
4. **Étape 3 — Extraction** — ~50 champs structurés extraits par appel LLM dense sur les
   documents de référence de chaque champ (couche 1), avec repêchage automatique sur tout le
   dossier pour les champs encore absents (couche 2 : scoring mots-clés **et** classement
   sémantique par embeddings, fusionnés — cf. `backend/app/extraction/semantic_retrieval.py`),
   recoupement multi-sources sur les champs critiques, checkpoint humain de correction.
5. **Phase 1 — Synthèse projet** — rapport narratif en 13 thèmes, généré en map-reduce : un
   relevé factuel par document pivot (texte intégral, jamais tronqué), puis un appel de
   rédaction par thème alimenté par ces relevés.
6. **Phase 2 — Audit des risques DO/TRC** — même principe map-reduce, 6 sections d'ouvrage
   (A→G), chacune produisant une liste de risques structurés référencés aux DTU/Eurocodes,
   enrichi par les données Géorisques du lieu du chantier.

Détail complet du fonctionnement des Phases 1 et 2 : `docs/PHASES_ANALYSE.md`.

Auth optionnelle par code d'accès à 4 chiffres (pas de compte email/mot de passe), désactivée
par défaut pour un usage local ; export des rapports en Word/Excel ; journalisation complète et
optionnelle de chaque appel API Mistral pour reconstituer le coût réel d'un run
(`AOP_API_CALL_LOG_DIR`, cf. `.env.example`).

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

## Distribution Windows (exécutable autonome, pour faire tester l'app)

Le workflow GitHub Actions `.github/workflows/build-windows-exe.yml` produit un
`AOP-v2.exe` autonome (PyInstaller `--onefile`, ne nécessite ni Python ni Node ni `uv` sur
le poste testeur) : frontend et `backend/config/*.yaml` sont embarqués dans l'exécutable.

- Lancer manuellement : onglet **Actions** du repo → *Build Windows executable* →
  **Run workflow** (ou `gh workflow run build-windows-exe.yml`), puis récupérer l'artefact
  `AOP-v2-windows` (contient `AOP-v2.exe`, `.env.example`, `docs/GUIDE_UTILISATEUR.md`).
- Pousser un tag `vX.Y.Z` publie en plus une Release GitHub avec le zip attaché.

Sur le poste testeur : dézipper, copier `.env.example` en `.env` à côté de `AOP-v2.exe` et
renseigner `MISTRAL_API_KEY`, puis double-cliquer `AOP-v2.exe` — le navigateur s'ouvre
automatiquement sur `http://127.0.0.1:8000`. `workspace/` (dossiers traités, cache OCR, DB
SQLite) et `.env` sont créés à côté de l'exécutable et persistent d'un lancement à l'autre
(build PyInstaller **ne fait pas** de cross-compilation : ce .exe doit être produit par le
runner `windows-latest`, pas construit localement depuis macOS/Linux).

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
(`monkeypatch`) ; quelques tests d'intégration valident le pipeline complet de bout en bout via
l'API réelle sur des documents natifs (texte dense, aucun OCR déclenché).

Au-delà de cette suite automatisée, de nombreux runs e2e réels (via l'API Mistral, sur de vrais
DCE) ont été effectués pendant le développement pour mesurer temps/coût/précision à chaque
évolution majeure (OCR différé, suppression puis réintroduction enrichie de la couche 2
d'extraction, map-reduce des Phases 1-2, parallélisation, clés API de secours...). Ces campagnes
et leurs rapports sont documentés dans `test-runs/README.md` — dossier **local, non versionné**
(dossiers clients + volumétrie), partagé séparément si besoin.

## Configuration (`backend/config/`)

- **`models.yaml`** — modèles Mistral utilisés (versions épinglées), seuils de confiance,
  concurrence/throttle des appels API. Les versions datées se périment côté API Mistral
  (modèles retirés) : si l'upload échoue en erreur `invalid_model`, mettez à jour les champs
  `model` concernés avec une version listée par `client.models.list()`.
- **`taxonomy.yaml`** — taxonomie de classement de l'étape 1 (catégories, mots-clés
  filename/contenu, lot-awareness).
- **`pieces_checklist.yaml`** — pièces attendues de l'étape 2 (complétude), par phase de
  marché.
- **`extraction_schema.yaml`** — les ~50 champs de l'étape 3 (extraction), leurs catégories de
  référence, mots-clés de repérage.
- **`synthese_projet_schema.yaml`** — les 13 thèmes de la Phase 1 (synthèse projet).
- **`audit_risques_schema.yaml`** — les 6 sections d'ouvrage de la Phase 2 (audit des risques).

Toute évolution de version de modèle ou de seuil se fait dans ces fichiers, jamais en dur
dans le code.

## Architecture

```
backend/
├── app/
│   ├── main.py              # FastAPI + WebSocket + montage du frontend buildé
│   ├── api/                  # routes REST + WebSocket (une route par étape/phase + auth)
│   ├── ingestion/             # dézip récursif, inventaire, routage extraction de texte
│   ├── ocr/                    # appel Mistral OCR haut niveau + cache persistant
│   ├── classify/                # étape 1 : taxonomie, moteur 3 signaux, renommage, copie triée
│   ├── completeness/             # étape 2 : checklist de pièces, moteur 3 couches
│   ├── extraction/                # étape 3 : moteur d'extraction + retrieval sémantique (couche 2)
│   ├── synthesis/                  # Phase 1 : synthèse projet (map-reduce)
│   ├── audit/                       # Phase 2 : audit des risques DO/TRC (map-reduce) + Géorisques
│   ├── auth/                         # code d'accès, session cookie, anti-brute-force
│   ├── mistral/                       # wrapper SDK bas niveau (retry, upload, OCR, chat structuré, logs)
│   ├── reports/                        # export Word des rapports Phase 1/2
│   ├── store/                          # modèles SQLAlchemy, session, repository
│   └── settings.py                     # config .env + config/*.yaml
├── config/*.yaml
└── tests/
frontend/                    # React + Vite + TypeScript + Tailwind
docs/                        # documentation technique (architecture, phases, guide utilisateur)
plan/                        # documents de planification d'origine (pré-implémentation)
refs/                        # matériel de référence fourni par le métier (schéma de données, golden-set)
test-runs/                   # campagnes de test e2e — local uniquement, non versionné
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
- `workspace/aop.db` (SQLite) : état des dossiers, inventaire, cache, classification,
  complétude, extraction — toute décision porte confiance, méthode, modèle+version et
  horodatage.
- `workspace/<dossier_id>/organized/` : copie triée générée à l'étape 1 (jamais la source).
- `workspace/<dossier_id>/organized_report.{json,md}` : rapport source → cible, confiance,
  justification, pour chaque fichier copié.

## Documentation complémentaire

- `docs/ARCHITECTURE.md` — description technique détaillée de toute l'application (schémas
  inclus).
- `docs/PHASES_ANALYSE.md` — fonctionnement détaillé des Phases 1 (synthèse) et 2 (audit).
- `docs/GUIDE_UTILISATEUR.md` — guide utilisateur (embarqué dans la release Windows).
- `docs/MODELES_MISTRAL_LIMITES.md` — limites de débit (rate limits) par modèle Mistral.
- `plan/` — documents de planification d'origine, rédigés avant l'implémentation (spécification,
  audit du backend, frictions métier, optimisations envisagées) : utiles pour comprendre le
  raisonnement initial, mais peuvent diverger du comportement actuel — se fier au code et à
  `docs/` en cas de contradiction.
- `refs/` — matériel de référence fourni par le métier (arborescence attendue d'un DCE, schéma
  des données à extraire, golden-set de rapports de référence pour valider les Phases 1/2).
