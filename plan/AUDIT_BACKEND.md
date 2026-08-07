# Audit backend — fiabilité, performance, dette technique

Audit réalisé en lisant l'intégralité du code backend (`backend/app/`, ~6 450 lignes)
réparti en trois passes croisées (store/API, ingestion/OCR, pipelines classification/
complétude/extraction) puis en revérifiant chaque constat significatif dans le code
source pour éliminer les faux positifs. Objectif : identifier ce qui peut casser en
usage réel (concurrence, appels réseau, fichiers), ce qui coûte du temps/argent
inutilement (appels LLM, I/O), et ce qui va freiner les prochaines évolutions
(duplication, absence de tests), pour prioriser les correctifs.

Chaque point indique où le vérifier (`fichier:ligne`).

---

## 1. Bug de concurrence vérifié — crash possible sur fichiers dupliqués dans un même lot

C'est le point le plus sérieux de cet audit : un scénario réel et courant peut faire
échouer tout un dossier.

- **`ingestion/pipeline.py:114-119`** traite les documents par lots de
  `ocr.max_concurrency` (3 par défaut) en vrai parallélisme (`asyncio.gather` sur des
  threads). **`ingestion/pipeline.py:166`** appelle
  `get_or_create_pending_text_cache(s, sha256, extension)` pour chaque document du lot.
  Or `TextCache.content_hash` porte une **contrainte unique en base**
  (`store/models.py:290` : `unique=True`). Si **deux documents du même lot ont le même
  hash de contenu** (fichier dupliqué dans le zip — exactement le cas que
  `OPTIMISATION.md:18` décrit comme fréquent dans les DCE réels : « `- Signature 1`,
  `COPIE_SAUVEGARDE`, CG répétés par lot »), les deux threads tentent de créer la même
  entrée `TextCache` en parallèle : le second `flush()`/`commit()`
  (`store/db.py:77-84`) lève une `IntegrityError`.
- Cette exception n'est interceptée nulle part dans `_process_document_text`
  (`ingestion/pipeline.py:151`), et `asyncio.gather` (`pipeline.py:117-119`) est appelé
  **sans `return_exceptions=True`** : elle remonte donc à travers toute la boucle
  d'ingestion, jusqu'au filet de sécurité générique `_run_pipeline_safely`
  (`api/dossiers.py:98-126`), qui bascule **tout le dossier** en `error` — alors que le
  seul problème réel est que deux fichiers identiques arrivaient dans le même petit lot
  de 3.
- Correctif simple : dédupliquer par hash **avant** de dispatcher les documents du lot
  vers les threads (traiter une seule fois chaque hash, propager le résultat aux autres
  documents partageant ce hash), ou passer `return_exceptions=True` à `gather` et gérer
  l'`IntegrityError` comme un cas normal (relire l'entrée existante plutôt que
  planter).

---

## 2. Fiabilité des appels API Mistral (OCR / LLM)

- **Aucun timeout sur les appels OCR.** `call_structured_chat`
  (`mistral/client.py:149-178`) passe bien `timeout_ms` dérivé de
  `llm.timeout_seconds`, mais `upload_file_for_ocr` et `call_ocr`
  (`mistral/client.py:101-144`) n'en définissent aucun. Combiné au `asyncio.gather` par
  lot (`ingestion/pipeline.py:117-119`), un seul appel OCR qui reste bloqué (silence
  réseau, connexion lente) fige indéfiniment tout le lot — donc toute la progression du
  dossier — sans qu'aucune erreur ne remonte à l'utilisateur, qui voit juste la barre de
  progression arrêtée.
- **Le retry ne couvre qu'un seul type d'erreur.** `_retry` (`mistral/client.py:77-98`)
  ne rattrape que `mistralai...MistralError`. Une erreur réseau bas niveau non
  enveloppée par le SDK (timeout `httpx`, coupure de connexion) n'est donc jamais
  retentée. Le backoff (`2**attempt`, plafonné à 30 s) est aussi uniforme : une erreur
  permanente (401, clé API invalide) est retentée 3 fois pour rien avant d'échouer,
  alors qu'un 429/503 mériterait au contraire d'attendre plus longtemps (pas de lecture
  du header `Retry-After`).
- **`assert last_error is not None; raise last_error`** (`mistral/client.py:97-98`)
  utilise `assert` comme structure de contrôle — désactivable avec `python -O`, à
  remplacer par un `if`/`raise` explicite.

---

## 3. Performance / parallélisme sous-exploité

- **Extraction de texte native bridée par la concurrence OCR.**
  `ingestion/pipeline.py:115` réutilise directement `ocr.max_concurrency` (3) comme
  taille de lot pour **tous** les documents, y compris ceux qui n'appellent jamais
  l'API (DOCX, XLSX/CSV, PDF déjà natif — I/O locale + CPU uniquement). Sur un dossier
  majoritairement natif, séparer le lot « nécessite OCR » (throttlé) du lot
  « extraction locale » (parallélisme large) accélérerait sensiblement le traitement.
- **Hachage SHA256 non parallélisé.** `ingestion/inventory.py` (appelé via
  `asyncio.to_thread` unique dans `pipeline.py:77-85`) hache chaque fichier
  séquentiellement. Sur un dossier de plusieurs centaines de fichiers/plusieurs Go,
  c'est un goulot d'étranglement avant même que l'extraction ne démarre — alors que
  cette étape est justement citée par `OPTIMISATION.md:6` comme « parallélisable (ne
  consomme pas de LLM) ».
- **Zips imbriqués extraits en séquence.** `ingestion/unzip.py:122-132` boucle sur les
  archives imbriquées une par une, alors que le module lui-même documente que les DCE
  contiennent fréquemment plusieurs zips par lot (« ASSURANCES LOT 1 ET 2.zip » etc.) —
  parallélisable comme l'est déjà l'extraction de texte.
- **`recompute_dossier_counters` et équivalents** (`store/repository.py:142-219`)
  chargent toute la table `Document`/`CompletenessEntry`/`ExtractionEntry` du dossier en
  mémoire puis comptent en Python (plusieurs passes `sum(1 for x in docs if ...)`) au
  lieu d'un agrégat SQL (`COUNT`/`CASE WHEN`). Sans gravité pour la complétude/
  extraction (volumes bornés par la config), mais `recompute_dossier_counters` opère
  sur `Document`, dont le volume suit la taille du DCE (potentiellement plusieurs
  centaines de fichiers).
- **`GET /api/dossiers` sans pagination** (`api/dossiers.py:179-182`) : tous les
  dossiers historiques sont chargés et sérialisés à chaque appel. Sans effet à l'échelle
  actuelle, deviendra sensible après plusieurs mois d'usage réel si rien ne purge la
  liste (voir aussi §1 de `FRICTIONS_EXPERT_METIER.md` sur l'absence de suppression —
  entre-temps corrigée, mais rien n'empêche la liste de grossir indéfiniment).
- **La complétude (étape 2) n'est pas batchée, contrairement à la classification et
  l'extraction.** Voir §4 ci-dessous — c'est le point de performance le plus
  structurant de cet audit.

---

## 4. Complétude : le seul des 3 pipelines LLM non batché

`OPTIMISATION.md` prescrit explicitement de batcher les appels LLM pour la
classification (§2 : « un appel pour 10 fichiers ambigus au lieu de 10 appels ») et
pour l'extraction (§3 : « un seul appel structuré » par document). Ces deux principes
sont bien implémentés (`classify/pipeline.py`, `extraction/pipeline.py`). Mais la
complétude n'a **aucune section dédiée** dans ce plan, et le code reflète cet angle
mort :

- **`completeness/pipeline.py:111-120`** + **`completeness/engine.py:176-263`** :
  1 appel LLM par **(pièce × document candidat)**, jusqu'à `MAX_LLM_CANDIDATES=3` par
  pièce, en boucle strictement séquentielle sur toutes les pièces sélectionnées.
- Pour un dossier avec ~25 pièces à vérifier dont une bonne part « peut être incluse
  ailleurs », ça peut représenter des dizaines d'appels LLM séquentiels non batchés —
  potentiellement l'étape la plus lente des trois alors qu'elle devrait être la plus
  légère (classification : quasi gratuite ; extraction : coût assumé et concentré).
- Piste symétrique à ce qui existe déjà pour l'extraction (`plan_layer2_calls`) :
  grouper par document candidat toutes les pièces qu'il pourrait couvrir, en un seul
  appel structuré par document plutôt que par paire pièce/document.

---

## 5. Nettoyage & cycle de vie des ressources

La fonctionnalité de suppression de dossier (`DELETE /api/dossiers/{id}`, ajoutée
récemment) a introduit un état nouveau — « un dossier peut disparaître » — que
plusieurs composants ne gèrent pas encore :

- **`TextCache` jamais garbage-collecté.** `store/repository.py:131-139`
  (`delete_dossier`) ne touche jamais la table `TextCache` — le commentaire du code
  l'explique (« peuvent être référencées par d'autres dossiers encore présents ») mais
  ne vérifie jamais si c'est réellement le cas. Si le dossier supprimé était le
  **seul** à référencer une entrée de cache, celle-ci (+ le fichier `.md` de texte sous
  `workspace/cache/text/`) reste orpheline pour toujours : aucune tâche de purge
  n'existe.
- **`ProgressManager` fuit en mémoire.** `progress.py:19,22,32-34,55` : `disconnect()`
  vide le `set` de connexions d'un dossier mais laisse la clé (désormais un set vide)
  dans `_connections`, et `_last_event[dossier_id]` n'est jamais purgé — y compris à la
  suppression d'un dossier, qui n'appelle jamais `progress_manager` pour nettoyer son
  état. Fuite modeste mais réelle sur un process longue durée.
- **Cache OCR sans éviction.** `ocr/cache.py` : `workspace/cache/text/` grossit
  indéfiniment, sans TTL ni taille max, et n'est pas versionné par modèle OCR — un
  document déjà en cache ne sera jamais ré-OCRisé après un changement de `ocr.model`,
  sauf changement de contenu. Compromis à assumer explicitement plutôt que subi.
- **`shutil.rmtree(dossier_dir, ignore_errors=True)`** (`api/dossiers.py:174`) avale
  silencieusement toute erreur de suppression (permissions, fichier verrouillé) sans
  log — un dossier supprimé en DB peut laisser des fichiers orphelins sur disque sans
  aucune trace pour le diagnostiquer.
- **`duplicate_of_dossier_id` peut pointer vers un dossier supprimé.** Rien ne nettoie
  cette référence quand l'original est supprimé — le lien « voir ce dossier » côté
  frontend pointera indéfiniment vers un 404.

---

## 6. Concurrence & cohérence des données (hors §1)

- **Suppression d'un dossier pendant qu'un pipeline tourne dessus.**
  `DELETE /api/dossiers/{id}` (`api/dossiers.py:163-176`) n'a aucun verrou vis-à-vis
  d'une tâche de fond en cours sur le même dossier. Les fonctions internes retombent
  sur des `assert dossier is not None` après un nouveau fetch (ex.
  `classification.py:130,135`, `completeness.py:176,203`, `extraction.py:134,160`),
  utilisés comme contrôle de flux sur un état qui peut désormais réellement changer
  sous le pied du code. Le `try/except Exception` englobant absorbe l'`AssertionError`
  (pas de crash serveur), mais le résultat observé — message générique, évènement WS
  « error » diffusé pour un dossier qui n'existe plus — est confus plutôt que propre.
  Un contrôle explicite (409/404) serait plus lisible.
- **Détection de doublon à l'upload en léger TOCTOU.** `api/dossiers.py:150-157` :
  deux uploads strictement simultanés du même zip peuvent chacun ne pas se voir l'un
  l'autre avant commit — l'un des deux échappe à l'avertissement de doublon. Impact
  faible (juste un avertissement UI manqué).
- **Pas de `PRAGMA foreign_keys=ON`.** Tous les `ForeignKey(...)` de `store/models.py`
  sont purement déclaratifs côté SQLAlchemy ; SQLite désactive les FK par défaut et
  `store/db.py` ne les active jamais. Fonctionne aujourd'hui par discipline
  applicative, mais aucune protection DB n'empêche une ligne orpheline en cas de futur
  bug (ex. ordre de suppression incorrect).

---

## 7. Sécurité

- **Aucune authentification/autorisation** sur les routes REST ni le WebSocket —
  cohérent avec un outil local mono-utilisateur, mais aucun garde-fou si l'app est un
  jour exposée au-delà de `localhost` (les DCE contiennent potentiellement des données
  sensibles).
- **Upload sans limite de taille.** `api/dossiers.py:143-148` : le zip est streamé par
  blocs de 1 Mo sans plafond avant même l'inspection du contenu.
- **Garde-fou zip bomb non cumulatif.** `ingestion/unzip.py:82-86` limite la taille
  décompressée à 2 Go **par archive individuelle**, mais pas sur l'ensemble de
  l'arborescence récursive (jusqu'à 8 niveaux, `MAX_NESTED_DEPTH`). Un zip contenant 8
  niveaux de zips imbriqués, chacun juste sous 2 Go, peut au final écrire &gt;10 Go sur
  disque pour un seul upload. (Le zip slip, lui, est correctement bloqué par
  `_safe_target`, `unzip.py:63-70`.)
- **CORS codé en dur alors que le port frontend est configurable.** `main.py:35-41`
  liste `http://localhost:5173`/`http://127.0.0.1:5173` en dur, alors que
  `settings.py:26` expose `frontend_port` comme variable d'environnement
  (`AOP_FRONTEND_PORT`). Changer le port via env casse silencieusement le CORS, avec
  une erreur cryptique côté navigateur — la config n'est pas la source de vérité de son
  propre middleware.

---

## 8. Duplication de code / dette architecturale

- **Trois pipelines quasi identiques.** `classify/{pipeline,engine,report}.py`,
  `completeness/{pipeline,engine,report}.py`, `extraction/{pipeline,engine,report}.py`
  répètent la même structure d'orchestration (compteurs → statut → diffusion WS →
  boucle par item → finalisation → diffusion) sur environ 120 lignes chacun
  (`classify/pipeline.py:56-131`, `completeness/pipeline.py:67-138`,
  `extraction/pipeline.py:86-275`). Une factorisation réduirait le risque qu'un
  correctif appliqué à une étape soit oublié dans les deux autres — déjà visible : la
  logique fine diffère légèrement d'une étape à l'autre sans raison métier évidente.
- **Trois endpoints « reopen » quasi identiques**
  (`api/classification.py:176-201`, `api/completeness.py:224-245`,
  `api/extraction.py:175-196`) : même structure exacte (fetch → vérifier le statut dans
  une liste blanche → appeler `reopen_*` → diffuser). Un helper générique paramétré par
  `(reopen_fn, allowed_statuses, target_status, stage, message)` réduirait la
  duplication.
- **Trois filets de sécurité identiques.** `_run_pipeline_safely`
  (`api/dossiers.py:98-126`), `_run_completeness_safely` (`api/completeness.py:81-93`),
  `_run_extraction_safely` (`api/extraction.py:66-78`) sont identiques à ~90 % (log,
  passage en erreur, diffusion WS).
- **`_compile(patterns)` (regex + `fix_word_boundary`) redéfinie à l'identique 3 fois**
  (`classify/taxonomy.py:53`, `completeness/pieces_checklist.py:46`,
  `extraction/extraction_schema.py:41`).
- **`Taxonomy.by_path`, `PiecesChecklist.by_id`, `ExtractionSchema.by_id`** : recherche
  linéaire à chaque appel plutôt qu'un dict précalculé — sans impact réel vu la taille
  des référentiels, mais trivial à corriger.
- **Colonnes `*_report_json_path` peu utiles.** `mark_reorg_applied`/
  `mark_completeness_validated`/`mark_extraction_validated`
  (`store/repository.py:165-172,194-201,222-229`) stockent un chemin en DB, mais les 3
  endpoints de lecture correspondants recalculent systématiquement ce même chemin
  depuis la config plutôt que de le lire — la colonne ne sert donc qu'à tester
  « un rapport existe-t-il ». Si un jour le chemin réellement écrit diverge de la
  constante, ces endpoints serviront silencieusement le mauvais fichier.
- **`ClassificationEntryOut` duplique une partie de `DocumentOut`**
  (`api/schemas.py:47-64` vs `79-103`) : `relative_path`, `filename`,
  `is_analyzable` existent dans les deux schémas pour représenter le même document —
  à resynchroniser manuellement si un champ est ajouté côté `Document`.

---

## 9. Robustesse face aux cas limites métier

- **`reorg.py:85`** (`shutil.copy2`) n'est entouré d'aucun `try/except` : si un seul
  fichier source est manquant/corrompu sur disque (incohérence DB/FS), l'application de
  toute la réorganisation plante à mi-parcours — les fichiers déjà copiés restent en
  place, le rapport n'est jamais généré, `mark_reorg_applied` n'est jamais appelé, et le
  dossier reste bloqué dans un état intermédiaire sans message clair pour l'utilisateur.
  Les tests de `test_reorg_apply.py` ne couvrent que le chemin nominal.
- **`_sanitize_lot_folder` (`reorg.py:44`)** ne filtre que `/` et `\`, pas les autres
  caractères invalides Windows (`:`, `*`, `?`, `"`, `<`, `>`, `|`) ni les noms réservés.
  Si un lot vient d'un texte libre extrait par regex/LLM, un nom comme
  `1: bâtiment A` produirait `LOT 1: bâtiment A`, invalide sous Windows. Mineur si le
  déploiement reste macOS/Linux.
- **Pas de validation de plage sur `confidence`** dans les 3 moteurs LLM
  (`classify/engine.py`, `completeness/engine.py`, `extraction/engine.py`) : aucun
  `Field(ge=0, le=1)`. Si le LLM renvoie une valeur hors `[0,1]` (ex. `95` au lieu de
  `0.95`), rien ne la borne — risque d'afficher une confiance absurde en UI. Gap
  identique dans les 3 moteurs, corrigeable en un seul validateur Pydantic partagé.
- **`_extract_native_pdf_pages` (`ingestion/text_extraction.py:111-114`)** capture
  `except Exception` et retombe silencieusement sur une liste vide **sans logger
  l'exception d'origine** — ce cas est traité exactement comme « PDF sans texte natif →
  OCR complet », ce qui masque un vrai bug pdfplumber/PDF corrompu sans aucune trace de
  diagnostic dans les logs.
- **Bug d'encodage potentiellement présent aussi dans `_extract_csv`.**
  `ingestion/text_extraction.py:312-320` essaie `utf-8-sig`/`utf-8`/`cp1252`/`latin-1`
  dans l'ordre et garde le premier décodage qui ne lève pas d'exception, **sans
  l'heuristique de plausibilité** (`_looks_like_mojibake`) qui vient d'être ajoutée pour
  les noms de fichiers zip (`ingestion/unzip.py`). Un CSV dans une page de code
  ambiguë peut ressortir en texte corrompu silencieusement — même classe de bug que
  celui déjà corrigé, probablement le même correctif à répliquer ici.

---

## 10. Couverture de tests

- **Aucun test unitaire dédié** à `run_classification_pipeline`,
  `run_completeness_pipeline`, `run_extraction_pipeline` : uniquement exercés via des
  tests d'intégration API lourds (`TestClient` + polling avec `time.sleep`, deadline
  20 s). Aucun test du comportement multi-lots de la classification, ni des erreurs LLM
  partielles au niveau orchestration (seulement testées au niveau `engine.py`).
- **`generate_synthesis` (`extraction/engine.py:429`) n'a aucun test** — ni le cas de
  succès, ni son échec silencieux (`except Exception: return None`).
- **`reorg.py` : seulement 3 tests**, aucun sur fichier source manquant/erreur I/O
  pendant la copie, ni sur `_sanitize_lot_folder` avec des caractères spéciaux.

---

## Pistes de priorisation

**Gains rapides, fort impact :**
1. Ajouter un timeout explicite sur `upload_file_for_ocr`/`call_ocr`
   (`mistral/client.py`) — aujourd'hui absent, contrairement à `call_structured_chat`.
2. Dédupliquer par hash avant de dispatcher un lot de documents vers les threads
   d'extraction, pour éliminer le crash `IntegrityError` sur fichiers dupliqués dans un
   même lot (§1 — bug vérifié, scénario courant sur des DCE réels).
3. Nettoyer `ProgressManager` et purger le cache `TextCache`/disque orphelin lors de la
   suppression d'un dossier (§5) — fuite mémoire + fichiers orphelins qui grossissent
   avec l'usage réel désormais que la suppression existe.
4. Corriger le CORS codé en dur pour qu'il suive `settings.frontend_port` au lieu d'une
   liste figée (`main.py` vs `settings.py`).
5. Borner `confidence` à `[0,1]` dans les 3 moteurs LLM (validateur Pydantic partagé).

**Plus structurant :**
6. Batcher les appels LLM de la complétude (étape 2), comme le sont déjà classification
   et extraction — potentiellement le gain de latence le plus important, absent du
   plan `OPTIMISATION.md` lui-même (§4).
7. Factoriser les 3 triplets `pipeline/engine/report` (classify/completeness/
   extraction) et les 3 endpoints « reopen » — réduire la dette de duplication avant la
   prochaine évolution (ex. ajout d'une 4ᵉ étape).
8. Rendre `apply_reorganization` résilient à un fichier source manquant (try/except par
   fichier au lieu de laisser planter toute l'opération à mi-parcours).
9. Ajouter des tests unitaires dédiés aux fonctions `run_*_pipeline` et aux branches
   d'erreur (réponse LLM invalide, fichier manquant, timeout) plutôt que de ne les
   couvrir que via des tests d'intégration API lents.
10. Plafonner la taille cumulée de décompression sur toute l'arborescence récursive de
    zips imbriqués (pas seulement par archive individuelle) pour fermer la fenêtre
    zip-bomb résiduelle.
