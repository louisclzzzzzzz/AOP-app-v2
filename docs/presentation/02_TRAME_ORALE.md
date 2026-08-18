# Trame orale — notes de présentation

Points clés à dire, pas un script à lire mot pour mot. Suit l'ordre de `00_PLAN_DIAPOS.md`.
Prévoir ~20-25 min de diapos + ~10-15 min de démo live + questions.

---

### Diapo 1-2 — Titre / sommaire

- Se présenter, rappeler la durée du stage et l'objectif : livrer un outil utilisable, pas un
  prototype jetable — d'où l'insistance sur les tests et la traçabilité tout au long du stage.
- Annoncer le déroulé : diapos jusqu'à la méthode de travail, puis démo live dans l'app.

### Diapo 3 — Contexte & problème métier

- Partir du vécu métier : un DCE, c'est un ZIP fourre-tout, pas un dossier organisé — l'expert
  perd du temps rien qu'à comprendre ce qu'il y a dedans avant de commencer à analyser.
- Donner un ordre de grandeur concret : jusqu'à 84 fichiers pour un seul DCE (CHU Rouen, un des
  dossiers de test).
- Insister : l'objectif n'est pas de remplacer l'expert, mais de lui faire gagner le temps du tri
  et de la première lecture, pour qu'il se concentre sur le jugement métier.

### Diapo 4 — Principe directeur

- C'est la diapo la plus importante pour la crédibilité du projet auprès de collègues sceptiques
  sur l'IA générative : **le produit est conçu pour qu'on puisse ne jamais avoir à lui faire
  confiance aveuglément**.
- Détailler concrètement ce que ça veut dire en pratique : chaque checkpoint humain, chaque
  citation cliquable, chaque "absent" plutôt qu'une valeur inventée, sont des conséquences
  directes de ce principe — pas des ajouts après coup.
- Le dossier source n'est jamais modifié : toute transformation (tri, etc.) produit une copie,
  l'original reste intact et consultable.

### Diapo 5 — Vue d'ensemble du pipeline

- Parcourir le schéma de gauche à droite en nommant chaque étape en une phrase.
- Bien marquer les 3 losanges (checkpoints) : c'est le moment où l'humain reprend la main, et
  aucune étape suivante ne démarre sans validation explicite.
- Préciser que les Phases 1/2 (à droite, en pointillés) sont optionnelles et à la demande — elles
  ne bloquent jamais le reste si elles échouent.

### Diapo 6 — Stack technique

- Rester très haut niveau ici, l'audience est technique mais ce n'est pas le sujet du jour.
- Un seul message à retenir : l'app est **locale/autonome**, seul le texte des documents part
  vers l'IA Mistral — rien d'autre n'est hébergé ailleurs.
- Mentionner l'exécutable Windows autonome si pertinent pour l'audience (facilite les tests par
  des collègues non techniques, sans installation).

### Diapo 7 — Étape 1 : classement

- Message : la règle simple avant l'IA, systématiquement — l'IA n'est sollicitée que pour les cas
  vraiment ambigus (nom de fichier générique type `scan001.pdf`, contenu qui contredit le nom...).
- Point de réassurance technique : la catégorie renvoyée par l'IA est **structurellement
  impossible à inventer** — le format de réponse de l'IA est contraint à la liste réelle des
  catégories métier, ce n'est pas une simple consigne dans le texte de la question qu'on lui pose
  (qu'elle pourrait ignorer), c'est une contrainte imposée au niveau du format de sa réponse.

### Diapo 8 — Étape 2 : complétude

- Même logique de sobriété : la checklist (16 pièces attendues, ex. CCTP, étude de sol G2 PRO,
  RICT) est vérifiée sans IA dès que c'est possible.
- L'IA n'intervient qu'en dernier recours, et toujours avec obligation de citer le passage exact
  qui justifie sa réponse.

### Diapo 9 — Étape 3 : extraction

- Chiffre à donner : 50 données extraites par dossier (montants, garanties demandées, équipe de
  maîtrise d'œuvre, dates, missions du bureau de contrôle...).
- Expliquer le principe économique : une lecture IA **par document**, qui répond en une fois à
  toutes les données pertinentes pour ce document — pas 50 lectures séparées par donnée.
- Le recoupement (croiser plusieurs sources pour une donnée sensible comme un montant) est fait
  par **comparaison programmatique**, pas par un appel IA de plus — moins cher, plus fiable,
  déterministe.

### Diapo 10 — Phases 1 & 2

- Présenter le principe commun avant les deux applications : on lit chaque document clé une seule
  fois, en entier (jamais tronqué), puis on rédige à partir de ces lectures — d'où le nom
  "map-reduce" si la question technique vient, mais rester sur "lire une fois, rédiger ensuite" à
  l'oral.
- Phase 1 = récit du projet (15 thèmes : identité, ouvrage, équipe, contexte du sol...).
- Phase 2 = audit des risques par corps d'état (A→G), avec un vrai plus métier : croisement avec
  les données publiques Géorisques (zone sismique, argiles, radon...) du lieu exact du chantier —
  un référentiel officiel confronté aux documents du dossier.
- Ces deux rapports référencent les DTU/Eurocodes pertinents pour chaque risque identifié.

### Diapo 11 — Citation vérifiable

- C'est la diapo à ralentir : c'est l'argument principal contre le scepticisme "l'IA hallucine".
- Décrire le geste concret : cliquer sur une pastille dans le rapport ouvre le PDF source, à la
  bonne page, avec le passage surligné.
- Donner le chiffre d'amélioration (15 % → 80 % de citations effectivement localisées) comme
  preuve que ce n'est pas resté un vœu pieux mais un sujet activement mesuré et amélioré.

### Diapo 12 — Modèles IA et pourquoi Mistral

- Répondre à la question qui vient presque toujours : "pourquoi pas ChatGPT/GPT-4 ?"
  - Fournisseur **européen** — sujet de souveraineté des données pertinent pour un acteur
    assurantiel français.
  - Fonctionnalité de **sortie structurée** (Structured Outputs) exploitée à fond dans tout le
    pipeline — sans elle, il faudrait parser du texte libre et espérer que l'IA respecte le
    format, avec le risque d'erreur que ça implique.
  - Fenêtre de contexte suffisante pour lire des documents entiers (CCTP, RICT) sans les découper.
- Un modèle par niveau d'exigence, pas le même partout : rapide/économique pour les tâches
  faciles, modèle plus puissant réservé aux tâches qui en ont vraiment besoin — c'est aussi un
  levier de maîtrise du coût.
- Versions de modèle **figées et datées** en production (pas de "-latest") : un choix de
  reproductibilité — le comportement de l'outil ne doit pas changer du jour au lendemain parce
  que le fournisseur a mis à jour son modèle par défaut.

### Diapo 13 — Coûts & performance

- Donner le chiffre choc en le contextualisant bien : **moins de 1 $ à 1 $ par dossier traité de
  bout en bout** (tri + complétude + extraction + les deux rapports d'analyse avancée), mesuré sur
  deux dossiers réels début août.
- Préciser honnêtement que ce chiffre date d'avant plusieurs évolutions (schéma d'extraction
  passé de 29 à 50 champs, nouvelle couche de recherche sémantique) — un run de mesure plus
  récent est en cours au moment de cette présentation ; si les chiffres sont disponibles le jour
  J, les utiliser à la place (voir `test-runs/campagnes/2026-08-18_.../cost.md` une fois généré).
- Expliquer où va l'argent, sans s'excuser : les deux rapports d'analyse avancée (synthèse +
  audit) concentrent l'essentiel du coût, logiquement — ce sont eux qui relisent le plus de texte
  en entier. C'est un choix assumé : lire intégralement plutôt que tronquer coûte plus cher, mais
  c'est directement ce qui a fait passer le taux de citations localisées de 15 % à 80 %.
- Performance : la parallélisation des appels IA (mesurée, pas devinée, sur le vrai compte utilisé
  en production) a divisé par ~5 le temps de l'extraction et réduit de 20 à 30 % le temps des deux
  rapports — à coût quasi identique, gain uniquement sur le temps d'attente de l'expert.

### Diapo 14 — Fiabilité

- 492 tests automatiques, aucun n'a besoin d'une vraie clé API payante — utile à savoir pour
  quiconque reprend le projet et veut vérifier que rien n'est cassé avant de livrer.
- Insister sur le mot "best-effort" : une panne d'un service externe (l'IA, ou l'API publique
  Géorisques) ne fait jamais planter tout le dossier — au pire un rapport partiel, jamais une
  perte de travail déjà validé par l'expert.
- Si la question du débit/rate-limit de l'IA vient : dire qu'aucun réglage de performance n'a été
  choisi au doigt mouillé — tout a été mesuré en conditions réelles sur le compte utilisé,
  documenté dans `docs/ARCHITECTURE.md` §11.5 pour qui veut le détail.

### Diapo 15 — Versionning GitHub

- 26 pull requests fusionnées en moins d'un mois (17 juillet → 13 août) — le message n'est pas le
  chiffre en soi, mais ce qu'il révèle : **développement par petites étapes testées**, jamais un
  gros bloc livré d'un coup à la fin du stage.
- Une branche par fonctionnalité, jamais de commit direct sur `main` — chaque changement passe par
  une Pull Request.
- Une intégration continue (CI) a été mise en place à mi-parcours : chaque push/PR relance
  automatiquement toute la suite de tests backend + le build et lint du frontend — un changement
  qui casse quelque chose est visible avant la fusion, pas après.
- Mentionner la distribution : image Docker unique pour un déploiement serveur (testée sur
  Fly.io), et un exécutable Windows autonome **généré automatiquement** par GitHub Actions à
  chaque tag de version — ça a permis de faire tester l'outil à des collègues sans qu'ils aient
  quoi que ce soit à installer.
- Point utile pour la passation : la méthode de travail (livraison phase par phase, tests verts
  avant de passer à la suite, documentation tenue à jour au fil de l'eau dans `docs/`) est aussi
  importante à transmettre que le code lui-même — c'est ce qui permet de reprendre le projet
  sereinement.

### Diapo 16 — Reste à faire

- Rester factuel et constructif, pas défensif — c'est normal qu'un projet de stage ait une suite.
- Pistes déjà identifiées pendant le stage (à ajuster selon vos priorités) :
  - le taux de citations localisées (80 %) laisse encore 1 citation sur 5 non retrouvée
    automatiquement dans le PDF ;
  - la récupération automatique du DCE depuis les plateformes de marchés publics ne fonctionne
    aujourd'hui que sur ~5 % des cas (la plupart des plateformes utilisent un captcha) — la
    veille elle-même (repérer qu'un avis existe) reste utile même sans récupération automatique ;
  - décider d'un cadre de déploiement définitif (le Fly.io actuel est un environnement de test) ;
  - mesurer le coût en usage réel dans la durée, au-delà des campagnes ponctuelles présentées ici.

### Diapo 17 — Transition démo

- Phrase suggérée : *"Plutôt que d'enchaîner les captures d'écran, on va le faire tourner devant
  vous sur un vrai dossier."*
- Fermer le diaporama, ouvrir l'application.

---

## Scénario de démo suggéré (hors diaporama, dans l'app)

Choisir un dossier déjà partiellement traité pour ne pas attendre les temps de traitement réels
en live (ex. un des dossiers de `test-runs/dossiers/dossier_test/`, déjà passé par le pipeline
lors des campagnes de test) :

1. **Écran liste des dossiers** — montrer le statut de chacun, expliquer en une phrase la machine
   à états (chaque dossier avance étape par étape, jamais en arrière sans réouvrir explicitement).
2. **Étape 1 — plan de classement** — ouvrir l'arborescence triée, montrer une correction manuelle
   possible (déplacer un document, changer sa catégorie) pour illustrer le checkpoint.
3. **Étape 2 — complétude** — montrer le tableau des pièces, une pièce "absente" avec sa
   justification, une pièce "présente" avec sa citation.
4. **Étape 3 — extraction** — ouvrir le tableau des 50 champs, cliquer sur une valeur pour ouvrir
   le volet de preuve et montrer le PDF surligné à la bonne page — **c'est le moment le plus
   parlant de toute la démo**, à ne pas rater.
5. **Rapport de synthèse ou d'audit** — dérouler un rapport, cliquer sur une pastille de citation
   multi-sources pour montrer le menu de sources.
6. **Export** — montrer le bouton de téléchargement (Markdown/Word/PDF) pour rappeler que le
   livrable final est un document classique, pas seulement un écran.

⚠️ Ne jamais cliquer sur les boutons "Générer"/"Régénérer" d'un rapport pendant une démo en direct
sur le serveur partagé si d'autres personnes l'utilisent en parallèle — ce sont de vrais appels
IA facturés, sur un débit d'API partagé. Préparer le dossier de démo à l'avance.

---

## Questions probables, et pistes de réponse

- **"Est-ce que ça marche pour d'autres types de marchés que la construction ?"** — Non,
  volontairement : la taxonomie, la checklist et les schémas d'extraction sont spécifiques au DCE
  construction/assurance DO-TRC. Étendre à un autre domaine demanderait de refaire ces
  référentiels (pas le moteur, qui est générique).
- **"Que se passe-t-il si l'IA se trompe ?"** — Rien n'est jamais validé sans passage par un
  checkpoint humain ; une valeur erronée reste corrigeable à la main, à tout moment, et chaque
  valeur affichée est vérifiable par sa citation.
- **"Combien ça coûte à l'usage, sur un mois ?"** — Donner le coût par dossier (diapo 13) x le
  volume de dossiers traités par mois si vous avez ce chiffre ; sinon, dire que c'est justement le
  suivi à mettre en place en usage réel (diapo 16).
- **"Pourquoi ne pas avoir tout automatisé, sans validation humaine ?"** — C'est un choix de
  produit assumé dès le départ (diapo 4) : le principe directeur du projet privilégie
  explicitement la fiabilité sur la vitesse — un outil de souscription assurance ne peut pas se
  permettre une valeur fausse non détectée.
- **"Le projet est-il maintenable après votre départ ?"** — Oui : documentation technique à jour
  dans `docs/`, 492 tests, historique Git avec des messages de commit explicites, et une
  méthodologie de livraison par petites étapes documentée qui peut être reprise telle quelle.
