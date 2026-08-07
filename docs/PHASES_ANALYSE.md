# Phases 1 & 2 de l'analyse — fonctionnement technique

Ce document explique comment fonctionnent les deux modules d'analyse « avancée » d'AOP :
- **Phase 1** — Synthèse narrative du projet
- **Phase 2** — Audit des risques (souscription DO / TRC)

Il s'adresse à qui doit comprendre, maintenir ou faire évoluer ces deux pipelines. Pour une
présentation métier générale de l'outil (étapes 0 à 3 : tri, complétude, extraction), voir
[GUIDE_UTILISATEUR.md](GUIDE_UTILISATEUR.md).

## 1. Où se situent ces deux phases

Le protocole d'analyse complet (`refs/PHASE ANALYSE/00_PROTOCOLE.md`) prévoit une Phase 0
(cartographie documentaire) suivie d'une Phase 1 (synthèse) et d'une Phase 2 (audit des risques).
Dans le produit, la Phase 0 est intégrée à la Phase 1 (section « Cartographie des documents
pivots » en tête du rapport), et les Phases 1/2 sont deux **actions complémentaires**, pas des
étapes obligatoires du pipeline principal :

```
Dépôt du ZIP
   │
   ▼
Étape 1 — Tri & renommage           ┐
Étape 2 — Vérification complétude   ├─ pipeline principal, séquentiel, avec
Étape 3 — Extraction des données    ┘  checkpoint de validation humaine à chaque étape
   │
   ├──► Phase 1 — Synthèse narrative   (déclenchée à la demande, une fois l'étape 3 lancée)
   └──► Phase 2 — Audit des risques    (idem)
```

Points communs aux deux phases (`app/synthesis/pipeline.py`, `app/audit/pipeline.py`) :

- **Déclenchement explicite**, jamais automatique : chaque phase a son propre endpoint REST
  (`POST /api/dossiers/{id}/synthese-projet/generate` et `POST /api/dossiers/{id}/audit-risques/generate`,
  `app/api/project_synthesis.py`, `app/api/audit.py`), appelé depuis le bouton correspondant du
  frontend (`ExtractionSheet.tsx`).
- **Condition d'exécution** : le dossier doit être au statut `extraction_review` ou
  `extraction_validated` (l'étape 3 doit avoir été lancée) — sinon HTTP 409.
- **Best-effort et jamais bloquant** : un échec ne fait jamais basculer `Dossier.status` en
  erreur — chaque phase a son propre statut dédié (`synthese_projet_status` /
  `audit_risques_status`, valeurs `not_generated` / `generating` / `done` / `error`) et son propre
  champ d'erreur. Le dossier reste utilisable normalement même si l'une des deux phases échoue.
- **Exécution en tâche de fond** (`BackgroundTasks` FastAPI) : l'endpoint répond immédiatement,
  le frontend fait ensuite un simple *polling* de `GET /api/dossiers/{id}` tant que le statut vaut
  `generating`. Volontairement **pas diffusées sur le WebSocket de progression** partagé du
  pipeline principal (`app/progress.py`), qui réassigne `Dossier.status` en bloc à chaque
  évènement — ça écraserait le statut réel du dossier.
- **Résultat** : un unique champ Markdown stocké en base (`synthese_projet_md` /
  `audit_risques_md`), régénérable à volonté (une nouvelle génération écrase la précédente).

## 2. Phase 1 — Synthèse narrative du projet

**Fichiers clés** : `backend/app/synthesis/pipeline.py` (orchestration), `engine.py` (génération),
`schema.py` (chargement du schéma), `backend/config/synthese_projet_schema.yaml` (les 13 thèmes),
`backend/app/api/project_synthesis.py` (endpoint REST).

### 2.1 Principe

Le rapport de synthèse est découpé en **13 thèmes narratifs** (identité de l'opération,
destination et ambition, qualification des travaux, économie du projet, équipe MOE, missions du
bureau de contrôle, synthèse RICT, justifications techniques, environnement/voisinage,
diagnostic de l'existant, récit du sol, niveaux des plus hautes eaux, agressivité chimique).
Chaque thème est une section du rapport final, avec son propre format de sortie (prose, tableau
Markdown ou liste à puces) défini dans `synthese_projet_schema.yaml`.

Deux modes de génération selon le thème (`source` dans le schéma) :

- **`extraction_fields`** (1 thème : identité de l'opération) — aucun appel LLM, simple
  reformatage déterministe de valeurs déjà résolues et validées à l'étape 3 (`nom_moa`,
  `adresse_moa`, etc.).
- **`documents`** (12 thèmes) — le texte intégral (natif ou OCR) des documents *pivots* du thème,
  listés dans `pivot_categories` (chemins de la taxonomie de classification, ex.
  `TECH/ETUDE DE SOL`, `TECH/RICT`, `TECH/NOTICE`), est relu en **map-reduce** (§2.2). C'est la
  différence fondamentale avec l'étape 3 (extraction) : là où l'extraction relève une valeur
  atomique courte, la synthèse produit un texte rédigé qui **relit les documents sources
  directement**, pas une reformulation des valeurs déjà extraites.

Un thème peut aussi recevoir un **bloc de « grounding »** (`grounding_field_ids`) : des valeurs
déjà validées à l'étape 3, injectées dans le prompt comme base à ne pas contredire sans le
signaler — ça garantit la cohérence entre l'étape 3 et la synthèse sans réextraction, et permet au
LLM de recouper explicitement CCTP / notice / RICT contre une donnée déjà certifiée.

### 2.2 Pipeline d'exécution (`synthesis/pipeline.py`)

Trois phases, toutes dédupliquées **par document** (jamais par couple document × thème) et toutes
en concurrence bornée :

```
run_project_synthesis_pipeline(dossier_id)
  │
  ├─ 1. OCR à la demande, dédupliqué
  │     Union de tous les documents candidats de TOUS les thèmes (ensure_document_ocr).
  │     Un document pivot partagé par plusieurs thèmes (ex. le RICT, pivot de 7 thèmes)
  │     n'est OCRisé qu'une seule fois, avant la génération, pas une fois par thème.
  │
  ├─ 2. "map" — un appel LLM par DOCUMENT pivot (summarize_document)
  │     Chaque document est relu INTÉGRALEMENT, sans troncature, en un seul appel qui
  │     couvre d'un coup tous les thèmes dont il est pivot (topics_for_document). Il en
  │     sort un relevé factuel par thème concerné, ou l'absence explicite d'information.
  │     Même dédup que l'OCR : le RICT est lu une fois et son relevé est réutilisé par
  │     les 7 thèmes qui s'appuient dessus.
  │
  └─ 3. "reduce" — un appel LLM par thème (generate_topic)
        Inchangé dans l'esprit, mais alimenté par les relevés de l'étape 2 au lieu des
        textes bruts tronqués. asyncio.Semaphore(4) partagé avec l'étape 2 : les thèmes
        sont indépendants entre eux, donc parallélisés plutôt qu'exécutés en séquence (le
        temps de synthèse était auparavant la somme de 12 appels LLM indépendants,
        190-400s par dossier). Le sémaphore reste prudent vis-à-vis d'un éventuel rate
        limit Mistral (tokens/minute) ; un 429 isolé est de toute façon absorbé par un
        backoff exponentiel côté client Mistral.
```

Le nombre d'appels LLM passe donc de 12 à *N documents pivots + 12* — c'est le prix payé pour ne
plus jamais tronquer ni exclure un document pivot (§2.3).

À la fin, le rapport assemblé (cartographie + 13 sections) est stocké dans
`Dossier.synthese_projet_md`, avec le statut `done` et l'horodatage de génération.

### 2.3 Sélection des documents : pourquoi le map-reduce

`select_topic_documents` (`synthesis/engine.py`) parcourt les `pivot_categories` du thème dans
l'ordre déclaré dans le YAML et retient tous les documents classifiés sous ces catégories.

**Avant** (concaténation des textes bruts dans un unique prompt par thème), deux plafonds
s'appliquaient — `SYNTHESIS_PER_DOCUMENT_MAX_CHARS = 60 000` caractères par document et
`SYNTHESIS_TOTAL_CONTEXT_MAX_CHARS = 300 000` caractères par thème — avec deux conséquences :

1. au-delà de 60 000 caractères, un document était **tronqué à plat depuis le début**, sans aucune
   sélection par pertinence (contrairement à l'étape 3, qui score les extraits par mots-clés) ;
2. une fois les 300 000 caractères épuisés, les documents pivots restants étaient **purement et
   simplement absents du prompt**, jamais vus par le LLM — jusqu'à 69 documents CCTP candidats
   pour 3 réellement envoyés (§`data/resultats_synthese_test/RAPPORT_TECHNIQUE_ANALYSE.md`).

**Depuis**, aucun budget ne s'applique au cas nominal : chaque document pivot est relu
intégralement à l'étape de map, et son relevé est toujours présent dans le prompt du thème.
L'ordre des catégories dans le YAML n'a donc plus qu'un rôle rédactionnel (l'ordre d'apparition
des relevés), là où il était auparavant critique — le commentaire du thème `destination_ambition`
documente un cas réel où l'inverser cassait le thème, le CCTP volumineux épuisant le budget avant
d'atteindre l'arrêté de PC.

Le seul chemin qui reste borné (`SYNTHESIS_FALLBACK_*_MAX_CHARS`) est le **repli best-effort** :
si le relevé d'un document n'a pas pu être produit (échec LLM, thème absent de la réponse), son
texte brut tronqué est réinjecté dans le prompt du thème plutôt que de perdre le document. Le
rapport final le signale (`relevé indisponible, extrait brut tronqué utilisé pour : …`).

### 2.4 Prompts système (extraits, `synthesis/engine.py`)

**Map** (`_MAP_SYSTEM_PROMPT`) — relevé factuel d'un document pour chacun de ses thèmes :

> N'utiliser QUE le contenu du document fourni (aucune déduction, aucune estimation) ; conserver
> les données telles qu'écrites (chiffres, unités, montants, classements réglementaires) ainsi que
> les références internes (numéro d'article, d'avis, de lot, de mission) ; **reprendre entre
> guillemets la formulation exacte** de toute affirmation qu'un autre document pourrait contredire
> (classement ERP, nombre de niveaux, surfaces, avis émis) ; marquer explicitement l'absence
> d'information plutôt que de meubler.

Pas de citation par phrase dans les relevés (c'était un besoin de vérification en phase de test) :
en production, la traçabilité repose sur la liste des fichiers exploités par thème
(`TopicOutcome.documents_used`, rendue sous chaque section). Le `grounding` n'est volontairement
**pas** injecté au map, pour que le relevé rapporte ce que le document dit vraiment plutôt que de
confirmer la valeur déjà connue — et que le reduce puisse encore voir les divergences.

**Reduce** (`_TOPIC_SYSTEM_PROMPT`) — rédaction de la section :

> *Tu es un expert en audit technique et souscription assurance construction (SMABTP)…*
> Le contexte fourni est un relevé factuel **par document**, chacun attribué à son fichier source.
> Règles impératives : n'utiliser que ces relevés et les données déjà validées, jamais rien
> inventer ; signaler explicitement une information absente ; **confronter les relevés entre eux
> et signaler toute divergence en nommant les deux fichiers sources**, sans trancher en silence ;
> citer le document source entre parenthèses après chaque donnée factuelle ; respecter le format
> demandé (prose / tableau / liste).

C'est l'attribution de chaque relevé à son fichier qui préserve la détection de contradictions
inter-documents (cas réel : classement ERP différent entre le CCTP et l'arrêté de PC).

Les deux étapes répondent avec un objet structuré via `call_structured_chat` (Structured Outputs
Mistral, `app/mistral/client.py`) : `_DocumentSummaryResponse { resumes: [{ theme_id,
apporte_des_informations, constats: [str] }] }` pour le map, `_TopicResponse { contenu: str }` pour
le reduce — un seul champ texte Markdown, sans le titre de section (déjà géré côté assemblage).

**Pourquoi `constats` est une liste et non un texte multi-lignes** : demander des puces séparées
par des `\n` *à l'intérieur* d'une chaîne JSON déclenche une pathologie du décodage contraint. Une
fois la chaîne refermée, le whitespace est toujours licite entre deux tokens JSON — la grammaire
n'oblige donc jamais le modèle à en sortir, et il peut boucler indéfiniment sur des espaces et
tabulations sans jamais atteindre la virgule suivante. La génération finit avortée par le serveur
(`finish_reason="error"`) ou coupée par `max_tokens`, laissant un JSON tronqué non parsable.
**9 des 30 appels de map du run e2e du 2026-07-29 sont tombés là-dedans**, sans jamais rattraper en
3 tentatives (le même document rejoué à T=0.7 bouclait encore : ce n'est pas un aléa de tirage).
Avec une liste, la grammaire attend `,` ou `]` après chaque élément : le modèle a un signal de
sortie fort au lieu d'une zone de whitespace libre. Rejeu des 9 documents avec ce schéma : 9/9
valides du premier coup.

### 2.5 Assemblage du rapport (`assemble_report`)

```
# Synthèse projet — Phase 1
## Cartographie des documents pivots        (tableau déterministe : type de document × nb fichiers × pivot ?)
## <Titre thème 1>
<contenu Markdown généré ou reformaté>
_Sources consultées : ..._ _(+N document(s) pivot(s) lu(s) sans information utile pour ce thème)_
## <Titre thème 2>
...
```

Le comportement est best-effort à chaque étape, sans jamais faire échouer la Phase 1 :

- un **document** dont le relevé échoue (étape de map) retombe sur son extrait brut tronqué dans
  les thèmes concernés, et la section le signale ;
- un **thème** en échec (exception LLM à l'étape de reduce) n'interrompt pas le pipeline : sa
  section affiche `_Section non générée (erreur : …)_` et les 12 autres restent générées
  normalement.

`synthese_projet_status="error"` n'est réservé qu'à une exception non rattrapée par le pipeline
lui-même.

## 3. Phase 2 — Audit des risques (DO / TRC)

**Fichiers clés** : `backend/app/audit/pipeline.py` (orchestration), `engine.py` (génération),
`schema.py` (chargement du schéma), `georisques.py` (intégration API publique),
`backend/config/audit_risques_schema.yaml` (les sections A→G), `backend/app/api/audit.py`
(endpoint REST).

### 3.1 Principe

Contrairement à la Phase 1 (un thème = une section narrative), la Phase 2 modélise l'audit en
**sections d'ouvrage A→G**, chacune produisant, via un unique appel LLM, une **liste de risques
structurés** plutôt qu'un texte libre :

| Section | Titre | Périmètre |
|---|---|---|
| A & B | Fondations, parties enterrées et dallage | étude de sol, RICT, CCTP fondations/gros-œuvre/VRD |
| C | Superstructure | structure, gros-œuvre, charpente |
| D | Étanchéité / Couverture / Toiture-terrasse | étanchéité, couverture, zinguerie |
| E | Façades et menuiseries extérieures | façades, ITE, bardage, menuiseries |
| F | Équipements techniques / ENR / Fluides | CVC, plomberie, électricité, ascenseurs, ENR |
| G | Aménagements intérieurs / Second œuvre | cloisons, sols, acoustique, PMR |

Chaque risque identifié par le LLM est un objet structuré (`RiskItem`, `audit/engine.py`) avec :
`statut` (🔴 critique / 🟠 modéré / 🟡 faible / 🟢 maîtrisé), `element_ouvrage`, `risque`, `alea`,
une description et une préconisation courtes (pour le tableau synoptique), puis pour l'analyse
détaillée : `expose_situation`, `analyse_expert` (référencée DTU/Eurocodes), `impact_assurabilite`,
`recommandations` (actions/documents à réclamer) et `source` (fichiers/articles cités).

`analyse_expert` et `recommandations` sont des **listes de chaînes** — un point de vérification /
une action par élément, chaque élément restant un paragraphe dense. Ce n'est pas un choix de style
mais la même précaution qu'en Phase 1 (§2.4, `constats`) : des puces séparées par des `\n` *à
l'intérieur* d'une chaîne JSON exposent le décodage contraint à une boucle dégénérée sur du
whitespace, qui tronque la réponse. La Phase 2 n'avait pas encore cassé, mais elle produisait déjà
le motif déclencheur — sur le run du 2026-07-29, 30/30 `recommandation` et 20/30 `analyse_expert`
contenaient des sauts de ligne, jusqu'à 19 dans un seul champ. Le passage en liste ne raccourcit
rien : ces deux champs *étaient* déjà des listes, écrasées dans une chaîne.

Le prompt système cadre explicitement le rôle : *« Expert Senior en Ingénierie des Risques
Construction, en charge de la souscription des polices Dommages-Ouvrage (DO) et Tous Risques
Chantier (TRC) chez SMABTP »*, avec consigne de raisonnement narratif (expliquer les phénomènes
physiques : corrosion, poinçonnement, tassement différentiel, poussée hydrostatique…), d'audit
transversal (cohérence entre lots, points de greffe existant/neuf, coactivité), de détection des
Techniques Non Courantes (procédés sous ATec/ATEx/Pass'Innovation), et de matérialité (2 à 5
risques réellement structurants par section, pas un inventaire exhaustif de micro-points).

### 3.2 Pipeline d'exécution (`audit/pipeline.py`)

```
run_audit_pipeline(dossier_id)
  │
  ├─ 1. OCR à la demande, dédupliqué
  │     Union des documents dans le périmètre d'AU MOINS une section (les CCTP hors sujet
  │     ne sont pas OCRisés inutilement — filtrage par mots-clés de lot AVANT l'OCR, sur le
  │     nom de fichier, cf. 3.3).
  │
  ├─ 2. Géocodage + interrogation Géorisques (best-effort, cf. 3.4)
  │     Adresse chantier → géocodage BAN → 6 endpoints Géorisques.
  │
  ├─ 3. "map" — un appel LLM par DOCUMENT pivot (summarize_document_for_audit)
  │     Chaque document est relu INTÉGRALEMENT, sans troncature, en un seul appel qui
  │     couvre d'un coup toutes les sections dont il est pivot (sections_for_document).
  │     Il en sort la liste de ses prescriptions, réserves et LACUNES pour chaque section.
  │     Le RICT, pivot de 6 sections sur le dossier de test, est lu une fois et rend
  │     ~157 constats répartis entre elles.
  │
  └─ 4. "reduce" — un appel LLM par section A→G en concurrence bornée
        asyncio.Semaphore(4) partagé avec l'étape 3, même logique que la Phase 1.
```

Le rapport assemblé (contexte Géorisques + tableau synoptique + analyse détaillée) est stocké dans
`Dossier.audit_risques_md`.

### 3.3 Sélection des documents par section

`document_matches_section` apparie un document et une section : catégorie pivot, plus un filtre sur
les catégories « par lot » (`LOT_FILTERED_CATEGORIES = {TECH/CCTP TRAVAUX, TECH/DPGF}`). Un gros
dossier compte souvent 15 à 25 CCTP (un par corps d'état), et un audit d'étanchéité n'a besoin que
du ou des CCTP étanchéité/couverture — pas des 25. Chaque section déclare une liste `cctp_keywords`
(insensible à la casse et aux accents, via normalisation Unicode NFKD) appliquée au nom de fichier
et au lot détecté. Les autres catégories pivots (RICT, étude de sol, notice) ne sont jamais filtrées.

Cet appariement sert dans les deux sens, à partir du même prédicat : `select_section_documents`
(les documents d'une section, pour le reduce) et `sections_for_document` (les sections d'un
document, pour le map et pour décider quoi OCRiser). Il ne porte que sur le nom de fichier et le
lot, disponibles **avant** l'OCR — c'est ce qui permet de ne jamais OCRiser un CCTP hors sujet.

**Pourquoi le map-reduce ici aussi.** L'ancien assemblage concaténait les textes bruts dans le
prompt de chaque section, sous les mêmes plafonds que la Phase 1 — et ils saturaient pour de bon :
sur le run du 2026-07-29, **369 661 caractères perdus en 10 troncatures**, le RICT rogné de 84 423 à
60 000 caractères dans **cinq** sections et purement absent d'une sixième (budget total épuisé),
l'étude de sol G2PRO ramenée de 180 562 à 60 000. Le map-reduce supprime ces pertes **et envoie
moins** : 940 281 caractères d'entrée contre 1 203 026 (-22 %), le RICT n'étant plus renvoyé cinq
fois tronqué mais lu une seule fois en entier. Seul le chemin de repli reste borné
(`AUDIT_FALLBACK_*_MAX_CHARS`), pour un document dont le relevé n'a pas pu être produit.

Deux réglages que le rejeu sur documents réels a rendus nécessaires, et qui valent comme mise en
garde générale sur les étapes de map :

- **le plafond de sortie doit être propre au map** (`llm.max_tokens_document_summary = 16000`) : un
  relevé couvrant 6 sections dépasse les 8000 tokens des autres appels. La réponse était alors
  coupée en plein JSON, et le rattrapage renvoyait un relevé de 6 constats au lieu de 157 — un
  appauvrissement massif que rien ne signalait, puisque l'appel finissait par « réussir » ;
- **le prompt doit calibrer le volume attendu et interdire les constats « méta »**. Sans consigne
  explicite (« compte en dizaines de constats », « ne relève jamais l'absence d'un sujet étranger à
  l'objet du document »), le modèle rendait un unique constat de synthèse sur des CCTP de lot —
  un CCTP Fondations spéciales passait de 1 à 65 constats une fois la consigne ajoutée.

### 3.4 Intégration Géorisques (`audit/georisques.py`)

Objectif : confronter les prescriptions des documents (étude de sol, CCTP, RICT) à un
**référentiel officiel de risques naturels** publié par l'État (georisques.gouv.fr), comme
l'exige le protocole (« vérifier le zonage Séisme, Inondation (PPRI) et Argiles (RGA) »).

**Étape 1 — résolution de l'adresse du chantier.** L'adresse vient en priorité du champ
d'extraction `adresse_chantier` déjà validé à l'étape 3. Si ce champ est vide, un fallback
dédié (`extract_chantier_address`) relit via un petit appel LLM l'arrêté de permis de construire,
la notice ou le RICT (dans cet ordre) pour en extraire l'adresse — sans quoi les dossiers où
l'étape 3 aurait manqué ce champ n'auraient jamais de contexte Géorisques (cas réel constaté sur
le dossier de test « Le Grand Pic »).

**Étape 2 — géocodage.** L'adresse texte est géocodée via l'**API Adresse du gouvernement**
(`api-adresse.data.gouv.fr/search`, base BAN) pour obtenir longitude/latitude et code INSEE de la
commune.

**Étape 3 — interrogation de l'API Géorisques** (`georisques.gouv.fr/api/v1`), 6 endpoints :

| Aspect | Endpoint | Donnée récupérée |
|---|---|---|
| `seisme` | `/zonage_sismique` (par lon/lat) | zone de sismicité |
| `rga` | `/rga` (par lon/lat) | exposition au retrait-gonflement des argiles |
| `radon` | `/radon` (par code INSEE) | classe de potentiel radon (1 à 3) |
| `inondation` | `/gaspar/risques` (par code INSEE) | libellés des risques recensés sur la commune, filtrés sur « inondation » |
| `cavites` | `/cavites` (par lon/lat, rayon 1 km) | nombre de cavités souterraines recensées |
| `mvt` | `/mvt` (par lon/lat, rayon 1 km) | nombre de mouvements de terrain recensés |

**Tolérance aux pannes** : toute la chaîne est *best-effort* — aucune fonction ne lève d'exception
vers l'appelant. Une adresse non géocodable, un endpoint en panne ou un timeout (12s) produit un
`GeorisquesReport` partiel (champs à `None`) avec une entrée dans `errors`, jamais un échec du
pipeline d'audit.

**Croisement avec l'audit.** Chaque section du schéma déclare les aspects Géorisques pertinents
(`georisques_aspects`, ex. `["seisme", "rga", "inondation", "cavites", "mvt"]` pour la section
fondations). `format_aspects_grounding` construit alors un bloc « Données publiques Géorisques…
référentiel officiel à confronter aux documents » injecté dans le prompt de la section, avec
consigne explicite de signaler toute divergence entre le référentiel officiel et l'étude de sol /
le CCTP. Une section « Contexte réglementaire — Risques naturels (Géorisques) », avec tableau
complet des 6 aléas et l'adresse géolocalisée, est en outre affichée en tête du rapport final,
indépendamment de son utilisation par telle ou telle section.

### 3.5 Assemblage du rapport (`assemble_report`)

```
# Audit des risques — Phase 2
## Contexte réglementaire — Risques naturels (Géorisques)     (tableau des 6 aléas + adresse géolocalisée)
## Tableau récapitulatif des risques                          (1 ligne par risque, toutes sections confondues)
## Analyse détaillée par section
### Sections A & B — Fondations...
[STATUT : 🔴] | [FONDATIONS] | [Tassement différentiel / Hétérogénéité du sol]
Exposé de la situation : ...
Analyse de l'Expert & Référentiel : ...
Impact Assurabilité : ...
Recommandation de levée de doute : ... Source : ...
--------------------------------------------------------------------------------
[risque suivant...]
### Section C — Superstructure...
...
```

Une section sans risque saillant l'indique explicitement (`_Aucun risque saillant identifié…_`)
plutôt que de rester vide silencieusement ; une section en échec affiche l'erreur sans bloquer les
autres — même logique best-effort que la Phase 1.

## 4. Modèles LLM et paramètres (`backend/config/models.yaml`)

- Modèle utilisé par les deux phases : **`mistral-large-2512`** (épinglé en version datée pour la
  reproductibilité ; `mistral-large-latest` reste disponible en dev), appelé via
  `call_structured_chat` (`app/mistral/client.py`) qui impose une réponse JSON conforme à un
  schéma Pydantic (Structured Outputs Mistral).
- `temperature: 0.0` partout (reproductibilité), relevée légèrement uniquement en cas de
  ré-essai après une réponse JSON malformée (`parse_retries: 2`).
- `max_tokens: 8000` — plafond généreux car les sections d'audit et thèmes de synthèse produisent
  de longs contenus structurés ; sans ce plafond, une réponse tronquée par le défaut du modèle
  casse le parsing JSON.
- `timeout_seconds: 300` — relevé spécifiquement pour absorber les gros contextes envoyés par la
  Phase 1 (jusqu'à ~100k tokens par appel).
- Concurrence LLM bornée à **4 appels simultanés** dans les deux pipelines
  (`_SYNTHESIS_LLM_CONCURRENCY`, `_AUDIT_LLM_CONCURRENCY`), avec re-essai automatique et backoff
  exponentiel sur une erreur 429 isolée côté client Mistral.

## 5. Rapport final téléchargeable

Les deux rapports Markdown (`synthese_projet_md`, `audit_risques_md`) sont affichés côte à côte
dans l'onglet Extraction du frontend (`ExtractionSheet.tsx`, rendu via le composant
`Markdown.tsx`). Le bouton **« Télécharger le rapport (.md) »** ne rappelle pas le backend : il
concatène côté client les deux rapports déjà en mémoire (en retirant le titre `#` dupliqué de
chacun via `stripLeadingHeading`) sous un unique en-tête `# Rapport d'analyse — <nom du dossier>`,
et déclenche le téléchargement d'un fichier `rapport_<nom_dossier>.md` via un `Blob` /
`URL.createObjectURL` (`downloadTextFile`, `ExtractionSheet.tsx`). Le rapport n'est donc
téléchargeable comme un tout que si les deux phases ont déjà été générées ; chaque section reste
aussi consultable indépendamment à l'écran.

## 6. Récapitulatif des fichiers

| Fichier | Rôle |
|---|---|
| `refs/PHASE ANALYSE/00_PROTOCOLE.md` | Protocole métier d'origine (spec rédigée par l'expert) dont les deux schémas YAML sont la traduction opérationnelle |
| `refs/PHASE ANALYSE/prompts.md` | Prompts de référence ayant servi de base aux prompts système actuels |
| `refs/PHASE ANALYSE/rapport_ref_gp.md` | Rapport de référence (dossier « Le Grand Pic ») utilisé pour calibrer/comparer les deux phases |
| `backend/config/synthese_projet_schema.yaml` | Les 13 thèmes de la Phase 1 |
| `backend/config/audit_risques_schema.yaml` | Les 6 sections A→G de la Phase 2 |
| `backend/config/models.yaml` | Modèles Mistral épinglés, budgets de tokens, concurrence, timeouts |
| `backend/app/synthesis/pipeline.py` | Orchestration Phase 1 (OCR dédupliqué puis génération concurrente) |
| `backend/app/synthesis/engine.py` | Génération d'un thème (prompt, sélection documents, budget de contexte, assemblage) |
| `backend/app/synthesis/schema.py` | Chargement/validation du schéma YAML de la Phase 1 |
| `backend/app/audit/pipeline.py` | Orchestration Phase 2 (OCR dédupliqué, Géorisques, génération concurrente) |
| `backend/app/audit/engine.py` | Génération d'une section (prompt, sélection documents filtrée par lot, assemblage) |
| `backend/app/audit/georisques.py` | Géocodage BAN + client API Géorisques (6 endpoints), grounding et rendu Markdown |
| `backend/app/audit/schema.py` | Chargement/validation du schéma YAML de la Phase 2 |
| `backend/app/api/project_synthesis.py` | Endpoint REST Phase 1 |
| `backend/app/api/audit.py` | Endpoint REST Phase 2 |
| `backend/app/mistral/client.py` | Client Mistral partagé (Structured Outputs, retries, backoff) |
| `frontend/src/components/ExtractionSheet.tsx` | Boutons de génération, affichage des deux rapports, fusion et téléchargement du rapport combiné |
| `data/resultats_audit_test/*/phase1_synthese.md`, `phase2_audit.md` | Exemples de rapports générés sur des dossiers de test |
