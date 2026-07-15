# AOP v2 — Plan de refonte

Application d'aide à l'analyse des DCE (Dossiers de Consultation des Entreprises) pour l'underwriting assurance construction (SMABTP / appels d'offres publics).

L'app traite un DCE en **3 étapes séquentielles avec validation humaine entre chaque étape** :
1. Réorganisation & renommage du dossier (copie triée, l'original n'est jamais modifié).
2. Analyse de complétude selon une liste de pièces configurable.
3. Extraction d'informations ciblées dans les documents.

**Principe directeur : la précision et la qualité des résultats priment sur le temps de traitement et le coût API.** Toutes les décisions de conception (choix de modèle, nombre de passes, redondance, OCR systématique) doivent être prises dans ce sens.

---

## 0. Décisions structurantes (validées)

| Sujet | Décision |
| --- | --- |
| Réorganisation étape 1 | **Copie triée** : génère un nouveau dossier organisé, ne touche JAMAIS au zip/dossier source. |
| Format d'application | **App web locale** (backend + UI web sur `localhost`). Drag-drop du zip, suivi live. |
| Enchaînement des étapes | **Checkpoints** : validation humaine après chaque étape avant de passer à la suivante. |
| Fournisseur IA | **API Mistral** exclusivement (OCR + LLM). |
| Langue | Interface et documents : **français**. |

---

## 1. Vue d'ensemble de l'architecture

```
Upload ZIP  ─►  Étape 0 : Ingestion + OCR (une seule fois, réutilisé par toutes les étapes)
                     │
                     ▼
              Étape 1 : Classification + Réorganisation (copie triée)  ──► [CHECKPOINT humain]
                     │
                     ▼
              Étape 2 : Complétude (checklist cochable + niveau de sûreté)  ──► [CHECKPOINT humain]
                     │
                     ▼
              Étape 3 : Extraction de données (schéma structuré)  ──► [CHECKPOINT humain]
                     │
                     ▼
              Export : dossier trié + rapports (JSON, Excel, PDF)
```

Point clé : **l'OCR est effectué une seule fois par document à l'étape 0** et son résultat (texte markdown + confiance + bounding boxes) est mis en cache. Les étapes 1, 2 et 3 consomment ce cache sans re-OCRiser. C'est ce qui permet la « analyse profonde » demandée sans exploser les coûts ni le temps.

---

## 2. Stack technique

Rien d'imposé hormis Mistral. Choix retenus pour robustesse et simplicité de lancement local :

- **Backend** : Python 3.11+ / **FastAPI** (API REST + WebSocket pour le suivi de progression).
- **Traitement asynchrone** : file de tâches en mémoire (ou **Celery + Redis** si on veut de la persistance/parallélisme ; commencer simple).
- **Frontend** : **React + Vite + TypeScript**, Tailwind. Servi par le backend en local. Drag-drop (zip), barres de progression par document et par étape via WebSocket.
- **IA** : SDK **`mistralai`** (Python).
  - **OCR** : `mistral-ocr-latest` (endpoint `/v1/ocr`) — renvoie markdown, blocs typés, **scores de confiance** et **bounding boxes**. On épingle une version datée en prod.
  - **LLM raisonnement/extraction** : `mistral-large-latest` (flagship). Pour les documents avec images/schémas, modèle multimodal (Pixtral / Mistral Medium 3.5). Structured Outputs (JSON schema) activé.
- **Stockage** : système de fichiers local + **SQLite** pour l'état des dossiers, le cache OCR et les résultats (précision + traçabilité).
- **Config** : fichiers **YAML/JSON** versionnés (voir §7) : taxonomie de classement, checklist de pièces (`liste_piece.md`), schéma d'extraction (`donnees_de_ref.md`), paramètres modèles.

---

## 3. Étape 0 — Ingestion & OCR (socle commun)

1. **Dézippage** dans un espace de travail isolé (`workspace/<dossier_id>/source/`). Gestion des zips imbriqués (ex. `ASSURANCES LOT 1 ET 2.zip`, `OS.zip`) : décompression récursive.
2. **Inventaire** : chaque fichier reçoit un ID, hash, taille, extension, chemin d'origine, aperçu. Les fichiers non exploitables (dépôt dématérialisé `.cle/.cry/.iv/.pli/.xml`, archives déjà extraites) sont marqués « non analysable » mais conservés.
3. **Extraction texte** :
   - PDF texte natif : extraction directe (rapide) **+** OCR de contrôle sur les pages à faible densité de texte (PDF scannés partiels).
   - PDF scanné / image / PDF « plans » : **OCR Mistral systématique** (précision prime).
   - DOCX/DOC : conversion + extraction.
4. **Cache OCR** persistant (SQLite + fichiers `.md` par document), avec confiance moyenne par document et par bloc. Ré-exécutable sur un seul document si besoin.
5. Métadonnées enrichies conservées pour les étapes suivantes : titre détecté, premières lignes, mentions clés (numéro de lot, « CCTP », « RC », « G2 », etc.).

---

## 4. Étape 1 — Réorganisation & renommage (copie triée)

### 4.1 Objectif
Produire un dossier cible propre à partir du DCE en vrac, sans jamais modifier la source. Classer chaque fichier dans une arborescence normalisée et **renommer les fichiers mal nommés**.

### 4.2 Règles d'arborescence (dérivées des dossiers triés par l'expert)

Analyse des 6 dossiers de référence (`arborescence.md`). Un dossier trié suit ce squelette récurrent (tous les nœuds ne sont pas toujours présents ; multi-lots => sous-dossiers par lot) :

```
<AAAA>_<TYPE_OPERATION>_<STATUT>/
└── AO<aa>_<COMMUNE>_<intitulé opération>/
    ├── 1.ETUDE BD/                 → AE (acte d'engagement), mémoire de gestion, déclarations sur l'honneur, listing pièces
    ├── ADMIN/                      → pièces administratives de la consultation
    │   ├── AAPC/                   → avis d'appel public à la concurrence
    │   ├── GAN/                    → guide/fiche accompagnement (missionnement AOP)
    │   ├── PF/                     → pièces financières / captures achat public
    │   └── RC (ou RC DCE)/         → règlement de consultation
    ├── ASS/                        → volet assurance
    │   ├── CCAP/                   → cahier des clauses administratives particulières (assurance)
    │   ├── CCTP (ou CCP)/          → CCTP assurance
    │   ├── RC/                     → RC assurance
    │   ├── GAN/  PF/               → guide / pièces financières assurance
    │   ├── ATT ASS/               → attestations d'assurance   (sous-dossiers ENT / MOE, Constructeurs / Prestataires intellectuels)
    │   ├── LISTE INTERVENANTS/     → CRC, liste des intervenants
    │   ├── DEROG COM/              → dérogations communales IARD
    │   └── MARCHE SIGNE/           → actes d'engagement & marchés signés, notifications
    ├── ENVOI DEMAT/                → dépôt dématérialisé
    │   ├── CANDIDATURE/            → DC1, DC2, KBIS, URSSAF, attestations fiscales/sociales, pouvoirs (préfixe « C. »)
    │   ├── OFFRE/                  → offres par lot (préfixe « O. »)
    │   └── COPIE DEPOT (ou depot)/ → preuve de dépôt + COPIE_SAUVEGARDE (.cle/.cry/xml — laissés tels quels)
    ├── QR (ou QUESTIONS REPONSES)/ → questions/réponses de la consultation
    └── TECH/                       → volet technique du DCE
        ├── CCTP TRAVAUX (ou CCTP)/ → CCTP par lot (nommage type « MLR_DCE_B.3.x_LOT 0y_CAHIER 0z_… »)
        ├── ETUDE DE SOL (ou G2PRO / ETUDES SOL / Geotechnique)/ → G1, G2 AVP, G2 PRO, G4, G5
        ├── PLANS/                  → plans (archi, structure, fluides, VRD… nommage « A.1.x »)
        ├── NOTICE (DESCRIPTIVE)/   → notice descriptive / notice archi
        ├── PLANNING/               → planning d'exécution
        ├── RICT (ou RIT)/          → rapport initial de contrôle technique
        ├── ARRETE PC / PC/         → permis de construire & arrêtés
        ├── CONTRAT MOE / CONTRAT CT (ou CT)/ → contrats maîtrise d'œuvre / contrôle technique
        ├── SOCABAT/                → études de risque / avis Socabat
        └── AUTRES (PIECES)/        → non classables
```

Le classement multi-lots crée des sous-dossiers `LOT 1 ET 2`, `LOT 3 4`, `offre-lot1`… quand le document porte un numéro de lot.

### 4.3 Moteur de classification (précision maximale)
Pour chaque fichier, décision de rangement + nom normalisé fondés sur **3 signaux combinés**, pas seulement le nom de fichier (souvent ambigu) :
1. **Nom de fichier** d'origine (regex + mots-clés : `RC`, `CCAP`, `CCTP`, `AAPC`, `G2`, `RICT`, `SOCABAT`, `DC1`, `KBIS`, préfixes `C.`/`O.`, `Lot n`…).
2. **Contenu OCR** (titre de page, en-têtes, mentions réglementaires) — décisif quand le nom ment.
3. **LLM classifieur** (`mistral-large`, sortie structurée) qui reçoit nom + extrait OCR + taxonomie et renvoie `{catégorie, lot, type_document, nom_normalisé, confiance, justification}`.

Convention de renommage proposée (normalisée, lisible) :
`[CATEGORIE]_[LOT le cas échéant]_[TYPE]_[libellé court].ext`
— toujours conserver le nom d'origine dans les métadonnées (traçabilité), ne jamais l'écraser sur la source.

### 4.4 Sortie de l'étape 1
- Un **plan de réorganisation** (mapping `source → cible + nouveau nom + confiance + justification`) présenté dans l'UI.
- L'utilisateur **valide / corrige / re-catégorise** (drag-drop dans l'UI) — **[CHECKPOINT]**.
- À la validation, l'app **copie** les fichiers dans `workspace/<dossier_id>/organized/` selon l'arborescence. Rapport de tri exporté (JSON + lisible).

---

## 5. Étape 2 — Analyse de complétude

### 5.1 Objectif
Sur la base d'une **liste de pièces configurable et cochable** (différente selon chaque dossier), déterminer pour chaque pièce attendue : **présente / absente / partielle**, **où** (fichier + page), et **avec quel niveau de sûreté**.

### 5.2 Liste de pièces (source : `liste_piece.md`)
La checklist métier (SMABTP, Alexandra LESUR) est organisée en **3 phases** ; l'app la charge depuis `config/pieces_checklist.yaml` (§7.2) et affiche des cases à cocher **groupées par phase** pour sélectionner les pièces recherchées **pour ce dossier**.

**Phase A — Pièces utiles dès la constitution du dossier (étude technique)** :
- Demande d'assurance SMABTP complétée & signée, avec extrait K-BIS < 3 mois et pièces d'identité des bénéficiaires effectifs.
- CCTP des entreprises et/ou devis descriptif.
- Jeu de plans complet (Masse / Façade / Coupe).
- Planning des travaux.
- Rapport d'étude de sol **minimum G2 PRO** (DTU 13.1).
- Rapport Initial du Contrôleur Technique (RICT).
- Liste des matériaux de réemploi.

**Phase B — Pièces nécessaires à l'établissement du contrat** :
- Copie DOC signée (Déclaration d'Ouverture de Chantier), à défaut 1er OS signé.
- Copie de l'arrêté du permis de construire (ou déclaration de travaux).
- Contrat(s) de Maîtrise d'Œuvre.
- Liste de tous les intervenants au chantier (MOE comprise).
- Attestations d'assurance **décennale valables à la date de la DOC**, de tous les intervenants par lot (MOE comprise).
- Référé préventif en cas d'avoisinant.

**Phase C — Pièces à fournir à la réception du chantier** :
- PV de réception par entreprise avec levée des réserves.
- Déclaration du coût définitif de l'opération.
- Rapport Final du Contrôleur Technique (RFCT).

Chaque pièce = { id, libellé, phase, alias/synonymes, catégorie attendue, obligatoire O/N, `peut_etre_inclus_dans_autre`, indices de détection }. Nombre de ces pièces sont typiquement **noyées dans d'autres documents** (ex. K-BIS et pièces d'identité dans la demande d'assurance ; attestations décennale par lot regroupées dans un marché signé ; DOC/OS dans un dossier « marché ») → l'analyse profonde de §5.3 est essentielle.

### 5.3 Détection « analyse profonde »
Point crucial demandé : **une pièce recherchée n'est pas toujours un fichier unique nommé comme tel — elle peut être noyée dans un autre document.** Le moteur doit donc, pour chaque pièce cochée, procéder en couches :
1. **Correspondance par fichier** : un document du dossier correspond directement à la pièce (nom + catégorie + contenu).
2. **Correspondance intra-document** : recherche sémantique dans le **contenu OCR de tous les documents** (ex. une attestation d'assurance incluse dans un PDF « marché signé », une étude G2 AVP en annexe d'un rapport, une clause de contrôle technique dans le CCAP). Utilise recherche par mots-clés/embeddings + vérification LLM sur les passages candidats.
3. **Vérification LLM** : le LLM confirme que le passage/document satisfait réellement la pièce attendue et renvoie une **preuve** (citation + localisation).

### 5.4 Niveau de sûreté
Chaque pièce reçoit un statut + un **score de sûreté** consolidé à partir de : confiance OCR des passages, force de la correspondance (fichier dédié > mention explicite > inférence), et confiance du LLM. Échelle proposée : `Certain / Probable / À vérifier / Absent`. Toute pièce « Probable/À vérifier » est accompagnée du passage justificatif pour revue humaine.

### 5.5 Sortie
Tableau de complétude (pièce | statut | sûreté | localisation | preuve) → **[CHECKPOINT]** → export.

---

## 6. Étape 3 — Extraction d'informations

### 6.1 Objectif
Récupérer une liste précise de données (définies dans `donnees_de_ref.md`, Feuil2) principalement depuis : **RC, CCTP/CCP assurance, CCAP, étude de sol G2 AVP/PRO, PRO, permis de construire, notice archi**, etc.

### 6.2 Données à extraire (source : `donnees_de_ref.md`)
Groupe principal (fichiers de réf. : RC, CCTP assurance, CCAP) :
Nom & adresse du MOA ; garanties demandées (TRC, DO, CNR, CCRD, RCMOA/RCMO, TRM) ; travaux neufs vs sur existant ; nom & adresse du chantier ; destination du bâtiment ; existence contrôle technique / RICT / bureau d'étude de sol / mission G2PRO ; montants HT / TTC / honoraires / existants ; équipe MOE (archi, BET structure, sol, contrôle, fluides…) ; nombre de bâtiments neufs / existants ; nombre de niveaux par bâtiment ; dates début / fin prévisionnelle ; réception échelonnée ; missions du bureau de contrôle ; étude de sol (G2 AVP, G2 PRO, G5).

Informations complémentaires à vérifier : distance des avoisinants ; référé préventif / constat d'huissier ; mission AV ; parties enterrées (notice archi/plans/CCTP) ; niveau des plus hautes eaux (EE/EB/EH, étude de sol) ; stratigraphie/lithologie (étude de sol).

### 6.3 Méthode d'extraction (précision prime)
- **Schéma structuré** (`config/extraction_schema.yaml` → JSON Schema) piloté par Mistral **Structured Outputs / Document Annotations**.
- **Routage par type de donnée vers les documents pertinents** (une donnée est cherchée d'abord dans ses fichiers de référence, puis élargie si absente). Évite le bruit et augmente la précision.
- **Extraction avec citation obligatoire** : chaque valeur renvoyée porte `{valeur, source (fichier+page), extrait justificatif, confiance}`. Rien n'est « halluciné » sans preuve ; valeur absente = `null` explicite + note.
- **Multi-passes / recoupement** pour les champs critiques (montants, dates, garanties) : croiser RC + CCAP + CCTP et signaler les incohérences.
- **Post-traitement** : normalisation (montants HT/TTC, dates ISO), déduction du niveau RCMO/TRC via le tableau de référence de `donnees_de_ref.md` (Feuil1) — utile pour préremplir l'analyse métier (à confirmer avec le métier).

### 6.4 Sortie
Fiche de synthèse (donnée | valeur | source | confiance) → **[CHECKPOINT]** → export Excel/PDF alimentant la suite de l'analyse.

---

## 7. Configuration (fichiers versionnés)

### 7.1 `config/taxonomy.yaml`
Taxonomie de classement de l'étape 1 : catégories, sous-dossiers cibles, mots-clés/alias, règles de renommage. Dérivée de §4.2.

### 7.2 `config/pieces_checklist.yaml` (source : `liste_piece.md`)
Liste des pièces de l'étape 2, groupées par phase (A/B/C, §5.2). Format :
```yaml
- id: etude_sol_g2pro
  libelle: "Rapport d'étude de sol minimum G2 PRO"
  phase: A
  alias: ["G2 PRO", "G2PRO", "étude géotechnique", "DTU 13.1"]
  categorie_attendue: TECH/ETUDE DE SOL
  obligatoire: true
  peut_etre_inclus_dans_autre: false
  indices: ["mission G2 PRO", "G2 PRO", "étude géotechnique", "fondations superficielles"]

- id: attestation_decennale_par_lot
  libelle: "Attestations d'assurance décennale valables à la date de la DOC (par lot, MOE comprise)"
  phase: B
  alias: ["RCD", "assurance décennale", "garantie décennale"]
  categorie_attendue: ASS/ATT ASS
  obligatoire: true
  peut_etre_inclus_dans_autre: true   # active l'analyse intra-document
  par_lot: true                        # vérifier la couverture lot par lot
  controle_date: DOC                   # attestation valable à la date de la DOC
  indices: ["responsabilité civile décennale", "garantie décennale", "attestation d'assurance"]

- id: doc_signee
  libelle: "Copie de la DOC signée (à défaut 1er OS signé)"
  phase: B
  alias: ["DOC", "déclaration d'ouverture de chantier", "OS", "ordre de service"]
  categorie_attendue: TECH/AUTRES
  obligatoire: true
  peut_etre_inclus_dans_autre: true
  fallback: "premier OS signé"
  indices: ["déclaration d'ouverture de chantier", "ordre de service"]
```
Claude Code générera l'intégralité du fichier à partir des 3 phases de §5.2 (toutes les pièces, avec leurs alias et indices).

### 7.3 `config/extraction_schema.yaml`
Schéma des données de l'étape 3 (§6.2), avec pour chaque champ : type, fichiers de référence prioritaires, indices de recherche, exigence de citation.

### 7.4 `config/models.yaml`
Modèles Mistral utilisés, versions épinglées, paramètres (température basse pour l'extraction, nb de passes de recoupement).

---

## 8. UI & suivi de progression

- **Écran d'accueil** : zone drag-drop du zip + liste des dossiers déjà traités (SQLite).
- **Suivi live (WebSocket)** : progression globale + par document (dézippage → OCR → classification), avec compteur pages OCR / documents traités.
- **Écran étape 1** : plan de réorganisation éditable (arborescence cible, renommages, confiance, justification), bouton « Appliquer la copie triée ».
- **Écran étape 2** : checklist cochable ; résultats avec statut, sûreté, localisation, extrait de preuve cliquable.
- **Écran étape 3** : fiche de données avec valeur, source cliquable (fichier+page), confiance, alertes d'incohérence.
- **Exports** : dossier trié (zip), rapports JSON, **Excel** (complétude + données) et **PDF** de synthèse.
- Chaque étape est **bloquée tant que la précédente n'est pas validée** (checkpoints).

---

## 9. Qualité, robustesse & traçabilité

- **Rien n'est perdu** : la source est immuable ; tout fichier non classé va dans `AUTRES` (jamais supprimé).
- **Traçabilité complète** : pour chaque décision (classement, présence de pièce, valeur extraite) → source, extrait, confiance, modèle & version, horodatage, en base.
- **Confiance affichée partout** ; tout ce qui est sous un seuil est explicitement marqué « à vérifier » pour revue humaine.
- **Reprise** : un dossier peut être relancé à n'importe quelle étape ; l'OCR est mis en cache et réutilisé.
- **Idempotence & coûts** : OCR une seule fois par document ; batch API là où pertinent (le coût reste secondaire vs la précision).
- **Tests** : jeu de dossiers de référence (les 6 de `arborescence.md`) comme golden set pour valider la classification ; cas de test « pièce noyée dans un autre document » pour l'étape 2.

---

## 10. Structure du dépôt

```
aop-v2/
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI + WebSocket
│   │   ├── ingestion/             # dézip, inventaire, extraction texte
│   │   ├── ocr/                   # client Mistral OCR + cache
│   │   ├── classify/              # étape 1 : classification + renommage
│   │   ├── completeness/          # étape 2 : checklist + analyse profonde
│   │   ├── extraction/            # étape 3 : données structurées
│   │   ├── mistral/               # wrapper SDK, retry, structured outputs
│   │   ├── store/                 # SQLite, cache, modèles de données
│   │   └── export/                # JSON / Excel / PDF
│   ├── config/                    # taxonomy, pieces_checklist, extraction_schema, models
│   └── tests/                     # golden set + cas « pièce noyée »
├── frontend/                      # React + Vite + Tailwind
├── workspace/                     # dossiers en cours (source immuable / organized / cache)
├── .env.example                   # MISTRAL_API_KEY
└── README.md
```

---

## 11. Phases de livraison

1. **Socle** : upload zip, dézip récursif, inventaire, OCR + cache, suivi WebSocket.
2. **Étape 1** : classification + plan de réorg éditable + copie triée + rapport.
3. **Étape 2** : checklist configurable + analyse profonde intra-document + niveaux de sûreté.
4. **Étape 3** : extraction structurée avec citations + recoupement + exports Excel/PDF.
5. **Durcissement** : golden set, seuils de confiance, incohérences, packaging local.

---

## 12. Points ouverts (à confirmer avec le métier)

- Faut-il **pré-calculer le niveau RCMO/TRC** (Feuil1 de `donnees_de_ref.md`) à partir des données extraites, ou laisser l'humain le faire ?
- **Format d'export** privilégié pour alimenter la suite (Excel type formulaire existant ? modèle imposé ?).
- **Volumétrie** typique d'un DCE (nb de fichiers, poids des plans) pour dimensionner le parallélisme OCR.
- Gestion des **plans/DWG** : OCR utile ou hors périmètre (extraction de données depuis les plans) ?
