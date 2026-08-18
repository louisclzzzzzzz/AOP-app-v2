# Diagrammes Mermaid — à exporter pour le diaporama

Chaque diagramme est délibérément **plus simple et plus visuel** que ceux de
`docs/ARCHITECTURE.md` (pas de noms de fichiers, de fonctions, de seuils numériques précis) : ils
sont pensés pour un public technique qui connaît les concepts IA (LLM, OCR, embeddings,
map-reduce...) mais pas ce projet — les modèles utilisés et les techniques clés (batch,
Structured Outputs, map-reduce) sont donc nommés, sans détailler l'implémentation. Référencés
depuis `00_PLAN_DIAPOS.md` par leur identifiant (D1, D2...).

**Comment exporter en image** (pour coller dans PowerPoint/Google Slides/Keynote) :
- le plus simple : coller le bloc de code dans [mermaid.live](https://mermaid.live), puis
  exporter en PNG ou SVG (fond transparent possible) ;
- ou l'extension "Markdown Preview Mermaid Support" de VS Code, clic droit → export ;
- ou en ligne de commande avec `mmdc` (`@mermaid-js/mermaid-cli`) si vous en exportez beaucoup
  d'un coup.

---

## D1 — Vue d'ensemble du pipeline

```mermaid
flowchart LR
    A["📦 Dossier déposé<br/>(ZIP)"] --> A2["📖 Ingestion<br/>OCR Mistral si document scanné"]
    A2 --> B["🗂️ Tri automatique<br/>LLM Mistral Small, par lot"]
    B --> C{{"✅ Validation<br/>humaine"}}
    C --> D["📋 Vérification de la complétude<br/>LLM Mistral Large"]
    D --> E{{"✅ Validation<br/>humaine"}}
    E --> F["🔎 Extraction des données clés<br/>LLM Mistral Large, 1 appel/document"]
    F --> G{{"✅ Validation<br/>humaine"}}
    G -.à la demande.-> H["📝 Synthèse narrative<br/>LLM Mistral Large, map-reduce"]
    G -.à la demande.-> I["⚠️ Audit des risques<br/>LLM Mistral Large, map-reduce"]

    style C fill:#fff3cd,stroke:#333,stroke-width:2px
    style E fill:#fff3cd,stroke:#333,stroke-width:2px
    style G fill:#fff3cd,stroke:#333,stroke-width:2px
    style H fill:#e2f0ff,stroke:#333
    style I fill:#e2f0ff,stroke:#333
```

## D2 — Stack technique en un coup d'œil

```mermaid
flowchart TD
    U["👤 Expert souscripteur"] --> WEB["🖥️ Interface web<br/>React + TypeScript"]
    WEB <-->|"REST + WebSocket<br/>(suivi en direct)"| SRV["⚙️ Serveur applicatif<br/>Python (FastAPI)"]
    SRV <--> DB["💾 Base de données locale<br/>SQLite — rien envoyé à l'extérieur"]
    SRV <--> IA["☁️ API Mistral (LLM + OCR)<br/>Structured Outputs (JSON contraint)<br/>uniquement le texte des documents"]
    SRV -.déploiement.-> DEPLOY["📦 Docker / Fly.io<br/>ou .exe Windows autonome"]

    style IA fill:#ffe8cc,stroke:#333
    style DB fill:#e2f0ff,stroke:#333
    style DEPLOY fill:#f0f0f0,stroke:#333
```

## D3 — Étape 1 : classement automatique (simplifié)

```mermaid
flowchart TD
    DOC["📄 Un document du dossier"] --> S1["🔤 Signal 1 : nom du fichier<br/>(règles/regex)"]
    S1 -->|match net| RULE["Classé par règle<br/>— sans LLM, gratuit et instantané"]
    S1 -->|"ambigu"| S2["📖 Signal 2 : contenu OCR/texte<br/>(règles/regex)"]
    S2 -->|match net| RULE
    S2 -->|"toujours ambigu"| IA["🤖 LLM Mistral Small<br/>1 appel par lot de documents ambigus (batch)<br/>catégorie contrainte (Structured Outputs)"]
    RULE --> PLAN["Plan de classement proposé"]
    IA --> PLAN
    PLAN --> CP{{"✅ Un expert valide<br/>ou corrige"}}

    style CP fill:#fff3cd,stroke:#333,stroke-width:2px
    style IA fill:#ffe8cc,stroke:#333
```

## D4 — Étape 2 : vérification de la complétude (simplifié)

```mermaid
flowchart TD
    LIST["📋 Liste des pièces attendues<br/>(16 pièces métier)"] --> L1{{"Déjà classée<br/>dans la bonne catégorie ?"}}
    L1 -->|oui| OK["✔️ Présente — sans LLM"]
    L1 -->|non| L2{{"Un autre document<br/>en parle-t-il ? (mots-clés)"}}
    L2 -->|non trouvé| KO["✖️ Absente — sans LLM"]
    L2 -->|"candidat(s) trouvé(s)"| IA["🤖 LLM Mistral Large<br/>1 appel par document candidat<br/>(regroupe plusieurs pièces à la fois)<br/>+ citation du passage exact"]
    IA --> RESULT["Présente / partielle / absente<br/>+ niveau de certitude"]

    OK --> CP{{"✅ Un expert valide<br/>ou corrige"}}
    KO --> CP
    RESULT --> CP

    style CP fill:#fff3cd,stroke:#333,stroke-width:2px
    style IA fill:#ffe8cc,stroke:#333
```

## D5 — Étape 3 : extraction des données (simplifié)

```mermaid
flowchart TD
    START["🎯 50 données à trouver<br/>(montants, garanties, équipe, dates...)"] --> READ["🤖 LLM Mistral Large<br/>1 appel riche par document de référence<br/>— jamais 50 appels séparés"]
    READ --> FOUND{{"Valeur trouvée et confirmée ?"}}
    FOUND -->|"oui, donnée sensible<br/>(montant, garantie, date clé)"| CROSS["🔁 Recoupement automatique<br/>entre plusieurs documents (sans LLM)"]
    FOUND -->|oui, donnée simple| VAL["Valeur retenue"]
    FOUND -->|non| WIDE["🔍 Recherche élargie automatique<br/>mots-clés + recherche sémantique<br/>(embeddings Mistral)"]
    WIDE --> VAL2["Trouvée ailleurs, ou<br/>déclarée absente avec justification"]

    CROSS --> CP{{"✅ Un expert valide<br/>ou corrige chaque valeur"}}
    VAL --> CP
    VAL2 --> CP

    style CP fill:#fff3cd,stroke:#333,stroke-width:2px
    style READ fill:#ffe8cc,stroke:#333
```

## D6 — Phases 1 & 2 : le principe commun (« lire une fois, rédiger ensuite »)

```mermaid
flowchart TD
    TRIGGER["🖱️ Déclenché à la demande<br/>par l'expert"] --> DOCS["📚 Documents clés du dossier<br/>(RICT, étude de sol, CCTP, notice...)"]
    DOCS --> MAP["🤖 « map »<br/>LLM Mistral Large, 1 lecture intégrale<br/>par document (jamais tronquée)"]
    MAP --> POOL["Relevé factuel de chaque document,<br/>réutilisable pour plusieurs sujets à la fois"]
    POOL --> REDUCE["🖊️ « reduce »<br/>LLM Mistral Large, 1 rédaction<br/>par thème/section à partir de ces relevés"]
    REDUCE --> REPORT["📄 Rapport final<br/>avec sources cliquables"]

    style MAP fill:#ffe8cc,stroke:#333
    style REDUCE fill:#ffe8cc,stroke:#333
```

*Pattern **map-reduce** : tous les appels « map » (un par document) puis tous les appels
« reduce » (un par thème/section) sont lancés en parallèle, à concurrence bornée, plutôt qu'en
séquence — sinon le temps total serait la somme de dizaines d'appels LLM indépendants.
Phase 1 = 15 thèmes narratifs (identité du projet, ouvrage, équipe, sol...). Phase 2 = 6 sections
de risques A→G (fondations, superstructure, étanchéité, façades, équipements, aménagements),
enrichies par les données publiques Géorisques du lieu du chantier.*

## D7 — Le mécanisme de citation vérifiable

```mermaid
flowchart LR
    MAP["🤖 Étape « map »<br/>chaque constat du LLM porte<br/>une étiquette de citation"] --> AFF["💬 Une affirmation<br/>dans le rapport"]
    AFF --> CHIP["🔗 Pastille source<br/>cliquable"]
    CHIP --> PREVIEW["📄 Le PDF s'ouvre<br/>à la bonne page"]
    PREVIEW --> HL["✨ Le passage exact<br/>est surligné"]

    style MAP fill:#ffe8cc,stroke:#333
    style CHIP fill:#ffe8cc,stroke:#333
```

*C'est ce qui transforme un rapport généré par IA en un document de travail auditable — chaque
phrase se vérifie en un clic, jamais "à la confiance".*

## D8 — Les modèles IA utilisés, et où

```mermaid
flowchart LR
    T1["Classement des documents<br/>(tâche jugée facile, par lot)"] --> M1["⚡ Mistral Small<br/>rapide et économique"]
    T2["Vérification de complétude"] --> M2["🧠 Mistral Large<br/>raisonnement/qualité<br/>Structured Outputs (JSON contraint)"]
    T3["Extraction des données"] --> M2
    T4["Synthèse narrative (map-reduce)"] --> M2
    T5["Audit des risques (map-reduce)"] --> M2
    T6["Documents scannés / plans"] --> M3["👁️ Mistral OCR<br/>lecture d'image dédiée"]
    T7["Documents très riches en images<br/>(repli)"] --> M4["🖼️ Mistral Medium<br/>multimodal"]
    T8["Recherche sémantique élargie<br/>(extraction, couche 2)"] --> M5["🧭 Mistral Embed<br/>similarité par vecteurs"]

    style M1 fill:#e2f0ff,stroke:#333
    style M2 fill:#ffe8cc,stroke:#333
    style M3 fill:#d9f2d9,stroke:#333
    style M4 fill:#f0e2ff,stroke:#333
    style M5 fill:#e2d9f0,stroke:#333
```

*Toutes les réponses LLM (Small et Large) passent par les **Structured Outputs** de l'API
Mistral : la réponse est contrainte à un schéma JSON strict, jamais du texte libre à reparser —
c'est ce qui rend une catégorie ou une valeur hors périmètre structurellement impossible à
renvoyer.*

## D9 — Répartition du coût par phase (run réel mesuré)

> ⚠️ Chiffres du 2026-07-29 (2 dossiers réels traités intégralement, coût total 1,73 $) — à
> remplacer par le run du 2026-08-18 une fois terminé (voir `00_PLAN_DIAPOS.md`, diapo 13).

```mermaid
pie showData title Répartition du coût par phase (total 1,73 $ pour 2 dossiers)
    "Synthèse narrative (Phase 1)" : 0.8517
    "Audit des risques (Phase 2)" : 0.4847
    "OCR" : 0.276
    "Extraction (étape 3)" : 0.0901
    "Classement (étape 1)" : 0.0168
    "Complétude (étape 2)" : 0.0114
```

## D10 — Méthode de travail & versionning GitHub

```mermaid
flowchart TD
    NEED["💡 Nouveau besoin<br/>ou correction"] --> BRANCH["🌿 Nouvelle branche dédiée<br/>(jamais de travail direct sur main)"]
    BRANCH --> DEV["👨‍💻 Développement +<br/>tests écrits en parallèle"]
    DEV --> PR["🔀 Pull Request ouverte"]
    PR --> CI["🤖 CI automatique (GitHub Actions) :<br/>pytest backend + build/lint frontend"]
    CI -->|✅ vert| REVIEW["👀 Revue"]
    CI -->|❌ rouge| DEV
    REVIEW --> MERGE["✅ Fusion sur main"]
    MERGE --> DEPLOY["📦 Déploiement<br/>(Docker/Fly.io, ou .exe Windows<br/>généré automatiquement)"]

    style CI fill:#fff3cd,stroke:#333
    style MERGE fill:#d9f2d9,stroke:#333
```

*26 pull requests fusionnées entre le 17 juillet et le 13 août 2026 — développement par petites
livraisons testées, jamais un gros bloc monolithique livré d'un coup.*
