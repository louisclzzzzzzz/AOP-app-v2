# Plan de présentation — AOP v2 (soutenance de stage / passation)

Support pour une présentation orale à des collaborateurs techniques qui ne connaissent pas le
projet. Objectif : montrer l'architecture, les modèles IA utilisés et les raisons de ces choix,
**sans entrer dans le détail du code**. Le déroulé prévu : diapos jusqu'au pipeline complet, puis
sortie du diaporama pour une démo live dans l'application.

Diagrammes Mermaid à exporter : voir `01_DIAGRAMMES_MERMAID.md` (un identifiant `D1`, `D2`... par
diagramme, référencé dans chaque diapo ci-dessous). Notes orales détaillées : voir
`02_TRAME_ORALE.md`.

**Chiffres clés vérifiés dans le code actuel** (commit `120c6f9`, 2026-08-18 — voir note de
fiabilité en fin de fichier) : 50 champs d'extraction / 11 sections, 15 thèmes de synthèse
narrative, 6 sections d'audit A→G, 32 catégories de taxonomie, 16 pièces de checklist de
complétude, 492 tests automatisés, 26 pull requests fusionnées entre le 2026-07-17 et le
2026-08-13.

---

## Diapo 1 — Titre

- AOP v2 — assistant IA pour l'analyse des DCE construction (souscription DO/TRC)
- Sous-titre : soutenance de stage / passation de projet
- Nom, dates de stage, SMABTP

## Diapo 2 — Sommaire

1. Contexte & problème métier
2. Principe directeur du produit
3. Vue d'ensemble du pipeline
4. Zoom sur chaque étape
5. Les modèles IA utilisés, et pourquoi
6. Coûts & performance mesurés
7. Fiabilité en conditions réelles
8. Méthode de travail & versionning GitHub
9. Ce qu'il reste à faire
10. → Démonstration live

## Diapo 3 — Contexte & problème métier

- Un DCE (Dossier de Consultation des Entreprises) arrive en vrac : plusieurs dizaines à
  plusieurs centaines de fichiers, zippés, mal nommés, parfois scannés.
- L'expert doit : trier, vérifier que les pièces attendues sont là, extraire les données clés
  (montants, garanties, équipe, dates...), puis rédiger une synthèse et un audit des risques —
  aujourd'hui en grande partie manuel.
- Volumétrie réelle observée sur les dossiers de test : de quelques dizaines à 84 fichiers pour
  un seul DCE (CHU Rouen).
- Risque : temps passé important, hétérogénéité entre analystes, risque d'oubli sur un dossier
  volumineux.

## Diapo 4 — Principe directeur du produit

Citation à l'écran (README) : *« La précision et la traçabilité priment toujours sur la vitesse
et le coût. »*

Quatre engagements concrets, à décliner en une phrase chacun :
- OCR systématique sur tout document scanné, avec un score de confiance par page.
- Toute donnée affichée est **citée** : document + passage exact.
- Aucune valeur n'est jamais inventée — une absence est signalée comme telle, jamais masquée.
- Le dossier source n'est **jamais modifié** : toute transformation produit une copie séparée.

C'est le fil rouge qui justifie presque tous les choix techniques présentés ensuite (checkpoints
humains, citations cliquables, catégories contraintes par schéma...).

## Diapo 5 — Vue d'ensemble du pipeline (D1)

- Diagramme D1 : du dépôt du ZIP aux deux rapports d'analyse avancée.
- Message clé : **3 checkpoints humains obligatoires** (classification, complétude, extraction) —
  le pipeline ne « décide » jamais seul au-delà de ce qui ne demande aucun jugement métier.
- Les Phases 1/2 (synthèse, audit) sont déclenchées à la demande, pas automatiques.

## Diapo 6 — Stack technique en un coup d'œil (D2)

- Diagramme D2 : utilisateur → interface web → serveur applicatif → base locale + IA Mistral.
- Message clé : application **locale et autonome** (un seul exécutable/conteneur), aucune donnée
  client hébergée ailleurs que sur le poste ou l'infra choisie (Fly.io en test) — seul le texte
  envoyé à l'IA Mistral part vers un tiers.
- Existe aussi en `.exe` Windows autonome (packagé automatiquement) pour faire tester l'outil
  sans installation.

## Diapo 7 — Étape 1 : classement automatique des documents (D3)

- Diagramme D3 (simplifié par rapport à ARCHITECTURE.md) : nom du fichier → contenu → IA en
  dernier recours.
- Message clé : l'IA n'est appelée **que pour les cas ambigus** — la majorité des documents sont
  classés par règles, sans coût ni latence IA.
- La catégorie proposée par l'IA est **structurellement impossible à inventer** : elle est
  contrainte à la liste réelle des catégories métier (mécanisme détaillé diapo 13).
- Checkpoint humain avant d'appliquer le tri définitif.

## Diapo 8 — Étape 2 : vérification de la complétude (D4)

- Diagramme D4 : checklist métier (16 pièces attendues) → 3 niveaux de vérification, du plus
  simple (déjà classé) au plus coûteux (vérification IA).
- Message clé : même logique « règle avant IA » qu'à l'étape 1 — l'IA n'intervient qu'en dernier
  recours, sur les documents candidats, jamais en force brute sur tout le dossier.

## Diapo 9 — Étape 3 : extraction des données (D5)

- Diagramme D5 : 50 données extraites (montants, garanties, équipe, dates, etc.), un seul appel
  IA riche par document plutôt qu'un appel par donnée.
- Message clé : recoupement automatique (sans IA, par comparaison programmatique) des valeurs
  critiques trouvées dans plusieurs documents — cohérent / incohérent / source unique.
- Checkpoint humain, correction possible valeur par valeur.

## Diapo 10 — Phases 1 & 2 : les rapports d'analyse avancée (D6)

- Diagramme D6 : le principe commun (« map-reduce ») aux deux phases — lecture intégrale de
  chaque document clé une fois, puis rédaction par thème/section à partir de ces lectures.
- Phase 1 = synthèse narrative en 15 thèmes (identité du projet, ouvrage, équipe, sol...).
- Phase 2 = audit des risques en 6 sections d'ouvrage (fondations → aménagements), enrichi par
  les données publiques Géorisques (sismique, argiles, radon, inondation...) du lieu du chantier.
- Ces deux rapports sont déclenchés à la demande, jamais automatiques ni bloquants.

## Diapo 11 — Le mécanisme différenciant : la citation vérifiable (D7)

- Diagramme D7 : d'une affirmation du rapport à la page exacte du PDF source, surlignée.
- Message clé : c'est ce qui transforme un rapport généré par IA en un **outil de travail
  vérifiable** plutôt qu'une boîte noire — chaque phrase peut être auditée en un clic.
- Chiffre parlant : sur un audit réel, le taux de citations effectivement localisables dans le
  PDF est passé de 15 % à 80 % après le renforcement de ce mécanisme.

## Diapo 12 — Les modèles IA utilisés, et pourquoi (D8)

- Diagramme/tableau D8 : une tâche → un modèle, jamais le même modèle partout.
- Pourquoi **Mistral** (pas OpenAI/Anthropic/Google) :
  - fournisseur européen — hébergement des données côté IA en Europe, point sensible pour un
    acteur assurantiel français ;
  - **Structured Outputs** natif : la réponse de l'IA est contrainte à un schéma JSON strict —
    c'est ce qui rend impossible pour l'IA d'inventer une catégorie ou un champ hors liste ;
  - fenêtre de contexte suffisante (~128k tokens) pour relire des documents entiers (RICT, CCTP)
    sans les tronquer bêtement ;
  - tarification à l'usage, sans engagement, adaptée à un stade de projet/POC.
- 4 modèles, chacun dimensionné à la tâche : un modèle « rapide/économique » pour les tâches
  faciles (classification), un modèle « raisonnement » pour les tâches à enjeu (extraction,
  synthèse, audit), un modèle OCR dédié, un modèle de repli pour les documents très riches en
  images.
- Réglages communs : température à 0 (reproductibilité), versions **datées et figées** en
  production (une IA ne doit pas changer de comportement sans qu'on le décide).

## Diapo 13 — Coûts & performance mesurés (D9)

- Diagramme D9 (camembert) : répartition du coût par phase sur un run réel de bout en bout.
- Chiffres 2026-07-29 (référence la plus complète disponible, méthodologie détaillée dans
  `02_TRAME_ORALE.md`) : deux dossiers réels traités intégralement (dépôt → audit des risques),
  coût total **1,73 $** pour les deux (0,71 $ et 1,02 $), dominé par les deux rapports d'analyse
  avancée (~78 % du coût à eux deux).
- ⚠️ **Section à compléter** : un nouveau run de mesure (dossiers Le Grand Pic + CHU Rouen) est en
  cours au moment de la préparation de cette présentation
  (`test-runs/campagnes/2026-08-18_demo-cout-temps-grand-pic-chu-rouen/`), sur le code actuel
  (schéma d'extraction à 50 champs, concurrence à 16, nouveau mécanisme de citations) — remplacer
  ces chiffres par les résultats de ce run une fois terminé, ils seront plus représentatifs de
  l'état actuel du produit.
- Message performance : la parallélisation des appels IA (mesurée empiriquement sur le vrai
  compte, pas supposée) a divisé par ~5 le temps de l'étape d'extraction, et réduit de 20 à 30 %
  le temps des deux phases d'analyse avancée — à coût quasi identique (paralléliser ne change pas
  le nombre de tokens consommés, seulement la manière de les envoyer).

## Diapo 14 — Fiabilité en conditions réelles

- 492 tests automatisés (aucun n'exige de vraie clé API — tout est simulé), exécutés à chaque
  push/PR par une CI GitHub Actions.
- Chaque défaillance externe est absorbée sans casser le pipeline : nouvelle tentative
  automatique en cas de saturation de l'API IA, repli sur un extrait brut si un document ne peut
  pas être lu intégralement, un rapport partiel plutôt qu'un échec total si une section échoue.
- Les limites réelles (débit maximal supporté par le compte IA, notamment) n'ont jamais été
  supposées : elles ont été **mesurées en conditions réelles** avant d'être utilisées dans le
  code (détail sur demande — pas nécessaire à l'oral).

## Diapo 15 — Méthode de travail & versionning GitHub (D10)

- Diagramme D10 : le cycle de vie d'une évolution, du besoin au déploiement.
- Une branche par fonctionnalité, jamais de travail direct sur `main`.
- Chaque évolution passe par une Pull Request, avec suite de tests + build/lint automatiques
  (CI) avant fusion.
- 26 PR fusionnées entre le 17 juillet et le 13 août 2026 — développement par petites
  livraisons testées, jamais un gros bloc monolithique.
- Distribution : image Docker unique (déploiement Fly.io testé), et exécutable Windows autonome
  généré automatiquement par GitHub Actions (pas d'installation Python/Node nécessaire côté
  testeur).

## Diapo 16 — Ce qu'il reste à faire / pistes de continuation

- (à personnaliser selon votre feuille de route — voir suggestions dans `02_TRAME_ORALE.md`)
- Fiabiliser encore le taux de citations localisées (80 % → ?)
- Étendre la veille BOAMP/JOUE (récupération auto du DCE limitée à ~5 % des plateformes)
- Décider d'un cadre de déploiement définitif (Fly.io / interne / poste local)
- Continuer à mesurer le coût réel en usage courant, au-delà des campagnes ponctuelles

## Diapo 17 — Transition vers la démo

- Une phrase de transition, ex. : « Plutôt que de vous montrer d'autres captures d'écran, on
  bascule directement dans l'application sur un dossier réel. »
- **Sortir du diaporama ici** → démo live dans l'app (voir trame orale pour le scénario suggéré).

## Diapo 18 (après démo) — Questions / merci

---

## Note de fiabilité de la documentation source

En préparant ces chiffres, deux écarts ont été trouvés entre `docs/ARCHITECTURE.md` et l'état
réel du code (vérifié directement dans `backend/config/*.yaml` et `backend/app/extraction/`) :

- `ARCHITECTURE.md` §8 indique 29 champs d'extraction (23+6) — le code en a **50** depuis la PR
  #18 (2026-07-31, refonte du schéma v2). La présentation ci-dessus utilise 50.
- `ARCHITECTURE.md` §8 ne mentionne pas la couche sémantique (embeddings) de la couche 2
  d'extraction, ajoutée par la PR #22 (2026-08-06) — présente dans le code
  (`backend/app/extraction/semantic_retrieval.py`) et mentionnée dans `README.md`.

Ce sont des détails de doc technique, sans impact sur le contenu de la présentation (volontairement
non technique), mais à corriger dans `ARCHITECTURE.md` si vous avez le temps avant la passation.
