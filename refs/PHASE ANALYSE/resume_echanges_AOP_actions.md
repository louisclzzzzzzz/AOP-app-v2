# Résumé métier des échanges AOP — extraction, pré-rapport et actions

## Sources utilisées

- `point app aop 1.txt` — échange du 9 juillet 2026, consacré à la définition des informations à extraire d’un dossier d’appel d’offres assurance construction.
- `point app aop 2.txt` — échange du 22 juillet 2026, consacré à la structuration des phases, au cadrage du prototype, au dataset CHU de Rouen et aux actions à mener.

---

## 1. Synthèse métier globale

Les deux échanges portent sur la conception d’un outil d’aide au traitement des appels d’offres publics en assurance construction. L’objectif est d’aider le conseiller ou le chargé de compte à exploiter rapidement un dossier souvent volumineux, mal rangé et redondant, afin de produire une première synthèse exploitable pour la souscription et le pré-rapport technique.

Le besoin n’est pas uniquement de rechercher des mots-clés. Il s’agit de construire une méthode industrialisable permettant :

1. d’identifier les bons documents dans un dossier d’appel d’offres ;
2. d’extraire les données structurantes du projet ;
3. de vérifier la complétude du dossier ;
4. de préparer une synthèse descriptive de l’opération ;
5. d’ouvrir ensuite vers des contrôles de cohérence et une première analyse de risques.

Le sujet est présenté comme une expression de besoin métier enrichie par un prototype. Le prototype sert à démontrer que le besoin est faisable, à préciser les données attendues et à faciliter ensuite le dialogue avec la DSI.

---

## 2. Vision cible du processus

Les échanges structurent progressivement un processus en plusieurs phases.

### Phase 0 — Cartographie et tri documentaire

La première étape consiste à cartographier le dossier reçu et à identifier les documents pivots. C’est un point important car les dossiers d’appel d’offres sont souvent transmis sous forme de ZIP contenant de nombreux doublons, des répertoires mal organisés et parfois plusieurs versions d’un même document.

Documents pivots évoqués :

- règlement de consultation ;
- CCAP assurance ;
- CCTP assurance ;
- CCTP gros œuvre, fondations, structure ;
- notice architecturale ou note de présentation ;
- étude géotechnique, notamment G2 AVP, G2 PRO ou G5 ;
- RICT, rapport initial de contrôle technique ;
- plans et pièces graphiques si nécessaire ;
- éventuellement diagnostics des avoisinants, référé préventif ou constat d’huissier.

L’objectif de cette phase est de déterminer quels documents sont réellement utiles pour l’analyse, d’écarter les doublons et de créer une base propre pour l’extraction.

### Phase 1 — Extraction des informations de base

La deuxième étape consiste à extraire les informations structurantes du dossier. C’est la partie qui doit être consolidée en priorité avant de passer aux API ou à l’analyse de risques.

Informations à extraire :

- maître d’ouvrage / souscripteur ;
- adresse du maître d’ouvrage ;
- nom de l’opération ;
- adresse du chantier ;
- désignation ou description de l’opération ;
- destination de l’ouvrage ;
- nombre de bâtiments ;
- distinction entre bâtiments neufs et bâtiments existants ;
- nombre de niveaux par bâtiment ;
- surfaces si disponibles ;
- montant des travaux ;
- honoraires et autres montants techniques quand disponibles ;
- valeur des existants en cas de réhabilitation ;
- date de début des travaux / DOC ;
- date prévisionnelle d’achèvement ;
- durée du chantier ;
- réception unique ou réceptions échelonnées ;
- existence de plusieurs tranches ou phases ;
- garanties demandées, par exemple DO, CCRD, CNR, RCMO selon les dossiers ;
- montants limites demandés pour les garanties ;
- intervenants : architecte, BET sol, BET structure, BET fluides, bureau de contrôle, etc. ;
- missions du bureau de contrôle ;
- présence ou absence d’une étude de sol ;
- type d’étude géotechnique disponible.

Cette extraction doit signaler les informations absentes plutôt que les inventer. Par exemple, si le montant des honoraires n’est pas disponible, le résultat attendu doit indiquer clairement que l’information n’a pas été trouvée.

### Phase 2 — Synthèse descriptive / pré-rapport

Une fois les données extraites, l’outil doit pouvoir produire une synthèse courte de l’opération, en quelques lignes, à partir des documents pivots.

Cette synthèse doit raconter le projet : nature de l’opération, bâtiments concernés, localisation, matériaux, contraintes principales, éléments existants, démolition éventuelle, travaux neufs, surfaces, niveaux et particularités techniques.

L’objectif est de produire un contenu réutilisable dans une fiche de missionnement, un pré-rapport ou un support interne.

### Phase 3 — Contrôles techniques et cohérence multi-documents

Les échanges insistent sur le fait qu’il ne faut pas seulement extraire l’information, mais aussi préparer des contrôles de cohérence.

Exemples de contrôles évoqués :

- comparer la classe d’agressivité des eaux ou du sous-sol entre l’étude de sol, le CCTP gros œuvre et le RICT ;
- vérifier la cohérence entre la stratigraphie, le type de fondations et les prescriptions techniques ;
- vérifier que les hypothèses géotechniques reprises dans les CCTP correspondent bien aux conclusions de l’étude de sol ;
- vérifier que les avis du bureau de contrôle sont correctement identifiés, notamment les avis suspendus, défavorables ou sans objet ;
- synthétiser les missions du bureau de contrôle et leurs remarques par thème.

Cette étape commence à aller au-delà de la simple extraction : elle mobilise une vraie analyse documentaire.

### Phase 4 — Analyse de risques et réponse assurantielle

L’analyse de risques est identifiée comme une étape ultérieure. Elle ne doit pas être traitée immédiatement tant que l’extraction descriptive et la structuration du pré-rapport ne sont pas stabilisées.

Les échanges évoquent néanmoins une approche cible avec :

- exposition de la situation ;
- analyse technique comme un expert ;
- impact potentiel sur les désordres ou pathologies ;
- préconisations en cas de levée de doute ;
- cotation de risque, par exemple vert / orange / rouge, avec intervention humaine éventuelle pour corriger.

Cette phase doit rester maîtrisée, car elle porte davantage sur l’appréciation technique que sur la simple restitution de données.

---

## 3. Données métier clés à intégrer dans le modèle

### 3.1 Destination de l’ouvrage

La destination de l’ouvrage doit être cadrée à partir de la taxonomie existante dans les questionnaires de risques SMABTP.

Principe proposé :

- si la destination trouvée correspond à une catégorie existante, la rattacher à cette catégorie ;
- si elle ne correspond pas, proposer une catégorie “Autre — …” ;
- exemple cité : un data center pourrait ne pas exister dans la taxonomie actuelle et devrait être proposé en nouvelle destination.

Cette structuration est importante car la destination de l’ouvrage peut avoir des impacts de tarification.

### 3.2 Typologie de l’opération

L’outil doit distinguer :

- construction neuve ;
- rénovation ;
- réhabilitation ;
- agrandissement ;
- opération mixte ;
- démolition partielle ;
- intervention sur existants ;
- bâtiments neufs et bâtiments existants dans un même programme.

Le nombre de bâtiments et le nombre de niveaux doivent être détaillés par bâtiment lorsque plusieurs bâtiments sont concernés.

### 3.3 Planning et réception

Les informations de planning sont importantes pour l’analyse assurantielle :

- date de début des travaux ;
- date prévisionnelle de fin ;
- durée du chantier ;
- réception unique ou réceptions échelonnées ;
- phasage ou tranches.

Les réceptions échelonnées sont identifiées comme un sujet important car elles peuvent modifier le démarrage des garanties.

### 3.4 Garanties demandées

L’outil doit identifier les garanties demandées dans l’appel d’offres et les montants limites associés.

Garanties citées :

- DO ;
- CCRD ;
- CNR ;
- RCMO.

Ces données peuvent être présentes dans le CCTP assurance, le CCAP assurance ou le règlement de consultation.

### 3.5 Bureau de contrôle et missions

Les missions du bureau de contrôle doivent être listées. Les échanges mentionnent notamment des missions réglementaires ou courantes comme :

- L ;
- LP ;
- LE ;
- AV ;
- SPS ;
- F ;
- PH ;
- TH.

Le besoin est de s’appuyer sur une liste fermée de missions connues, avec code et libellé, afin de pouvoir identifier les missions présentes dans les documents.

### 3.6 Études géotechniques

Les études géotechniques à rechercher prioritairement sont :

- G2 PRO ;
- G2 AVP ;
- G5.

La G2 PRO est considérée comme la référence la plus complète à ce stade du projet. La G2 AVP peut permettre de travailler avec des hypothèses. La G5 peut être utile si elle existe. Les missions G3 et G4 sont plutôt liées aux travaux et ne sont pas attendues dans un appel d’offres en amont.

L’absence d’étude de sol ou de RICT doit être clairement signalée comme point d’arrêt ou point de vigilance pour l’ingénieur.

---

## 4. Contenu technique à extraire dans les études de sol

Les échanges détaillent les informations attendues dans les études géotechniques.

### 4.1 Stratigraphie / lithologie

L’outil doit extraire la composition du sous-sol sous forme structurée.

Mots-clés et notions :

- stratigraphie ;
- lithologie ;
- composition du sous-sol ;
- couches de sol ;
- description du sous-sol.

Format attendu : tableau avec les couches, les profondeurs et la nature des sols.

### 4.2 Hydrogéologie et niveaux d’eau

L’outil doit rechercher les niveaux d’eau et les informations hydrogéologiques.

Notions citées :

- niveau des plus hautes eaux ;
- EBE ;
- EE ;
- EC ;
- EH ;
- niveau d’eau en phase chantier ;
- crues décennales ;
- crues exceptionnelles ;
- nappe phréatique ;
- piézomètres.

Ces informations sont importantes pour les fondations, les parties enterrées, le pompage éventuel en phase chantier et le besoin d’étanchéité ou de cuvelage.

### 4.3 Agressivité du sous-sol et des eaux

L’outil doit extraire la classe d’agressivité indiquée dans l’étude de sol, puis vérifier si elle est reprise de manière cohérente dans les autres documents.

Classes citées :

- XA1 ;
- XA2 ;
- XA3.

L’enjeu est de vérifier que la formulation du béton et les prescriptions de fondations sont cohérentes avec l’agressivité mesurée.

---

## 5. Avoisinants et existants

Les avoisinants constituent un sujet de vigilance technique et assurantielle.

Informations à rechercher :

- présence de bâtiments avoisinants ;
- distance des avoisinants, notamment seuil de 10 mètres ;
- présence d’un référé préventif ;
- présence d’un constat d’huissier ;
- diagnostic des avoisinants ou des existants ;
- mission AV du bureau de contrôle ;
- activité des locaux avoisinants ;
- risque potentiel de perte d’exploitation ;
- contraintes liées à la continuité d’activité.

L’objectif est d’abord de restituer les informations présentes dans le dossier, puis éventuellement de synthétiser l’avis du bureau de contrôle lorsqu’il existe.

---

## 6. RICT et avis du bureau de contrôle

Le RICT est identifié comme un document pivot important.

L’outil doit pouvoir :

- identifier les missions traitées ;
- repérer les avis favorables, suspendus, défavorables ou sans objet ;
- synthétiser les avis suspendus ou défavorables ;
- restituer les attendus du bureau de contrôle ;
- gérer les cas où les avis sont indiqués par logo ou pictogramme, ce qui peut nécessiter une analyse d’image ou une extraction plus robuste.

Une option évoquée consiste à ne synthétiser que les avis suspendus, car ils constituent souvent les points à traiter en priorité.

---

## 7. API et données externes

Les API sont vues comme une étape suivante, à traiter après consolidation de l’extraction documentaire.

API / sources évoquées :

- Géorisques ;
- risques naturels ;
- PPRI ;
- zones inondables ;
- retrait-gonflement des argiles ;
- risques miniers ;
- carrières ;
- affaissements ;
- vides karstiques ;
- dissolution de gypse ;
- radon.

Le prototype doit transformer l’adresse du chantier en coordonnées GPS pour interroger les API. Un point d’attention est de choisir les bonnes variables dans le catalogue de données, car un premier test a montré un risque d’erreur de variable.

---

## 8. Prototype, répétabilité et industrialisation

Le prototype existant a été construit avec du code / “vibe coding” et un LLM. Il permettrait déjà :

- de charger plusieurs documents ;
- de lancer une analyse ;
- d’extraire des données ;
- de signaler les divergences entre documents ;
- d’exporter un résultat en HTML ;
- de préparer un copier-coller dans une fiche ou un rapport.

Un point important est la répétabilité : sur un même dataset, le prototype doit donner les mêmes réponses à chaque exécution.

Le prototype n’est pas présenté comme une solution DSI définitive, mais comme un démonstrateur métier permettant de préciser l’expression de besoin et de montrer qu’une voie technique est possible.

---

## 9. Jeu de test CHU de Rouen

Le dataset CHU de Rouen est présenté comme le cas de test principal.

Il doit servir à :

- tester la répétabilité de l’extraction ;
- comparer le résultat automatique avec un rapport rédigé par un expert ;
- valider les champs attendus ;
- travailler sur un dossier réel avec plusieurs sources ;
- s’arrêter dans un premier temps à la synthèse descriptive et aux premières pages du rapport expert, avant l’analyse de risques détaillée.

Éléments à partager ou utiliser :

- dataset CHU de Rouen ;
- sélection de sources déjà faite ;
- rapport de l’expert ;
- sorties Notebook LM ;
- rapport / export du prototype ;
- fichiers de référence utilisés pour l’extraction.

---

## 10. Confidentialité et gouvernance des données

Les échanges insistent fortement sur la confidentialité.

Points de vigilance :

- ne pas entraîner de modèles avec les données internes ;
- éviter que des informations SMABTP, des rapports d’expertise ou des données de sinistres se retrouvent indirectement dans des résultats de modèles ;
- faire attention aux données personnelles, adresses de personnes physiques et éléments liés à des sinistres ;
- privilégier les outils ou configurations où l’option “zéro entraînement” est activée ;
- cadrer l’usage de Claude, Notebook LM, Gemini ou autres outils externes selon les règles de confidentialité.

Le principe métier formulé est clair : pas d’entraînement de modèles avec les données internes.

---

## 11. Actions à faire

### Priorité 1 — Consolider l’extraction de données

| Action | Responsable pressenti | Résultat attendu |
|---|---|---|
| Reprendre les champs d’extraction définis dans les échanges | Équipe projet / développeur prototype | Tableau propre des données à extraire |
| Reproduire l’extraction sur les 4 ou 5 documents pivots du CHU de Rouen | Développeur prototype | Résultat répétable sur le dataset test |
| Ajouter les garanties demandées et montants limites | Développeur prototype | Champs DO, CCRD, CNR, RCMO et montants associés |
| Ajouter la destination de l’ouvrage selon la taxonomie SMABTP | Métier + développeur | Catégorie normalisée ou proposition “Autre — …” |
| Signaler explicitement les informations absentes | Développeur prototype | Sortie fiable, sans information inventée |

### Priorité 2 — Structurer les documents et les livrables

| Action | Responsable pressenti | Résultat attendu |
|---|---|---|
| Catégoriser les documents du dossier d’appel d’offres | Développeur prototype | Cartographie documentaire phase 0 |
| Identifier les documents pivots par type d’information | Métier + développeur | Méthode entonnoir : où chercher quoi |
| Créer un format de sortie pré-rapport | Équipe projet | Synthèse réutilisable par le conseiller |
| Prévoir un export exploitable | Développeur prototype | HTML, Word ou format copiable dans une fiche |

### Priorité 3 — Exploiter le cas test CHU de Rouen

| Action | Responsable pressenti | Résultat attendu |
|---|---|---|
| Partager le dataset CHU de Rouen | Louis | Dossier de test complet ou sélection de sources |
| Partager le rapport de l’expert | Louis | Référence humaine pour comparer le résultat automatique |
| Partager les sorties Notebook LM / prototype | Louis | Exemples de format attendu |
| Comparer extraction automatique et rapport expert | Équipe projet | Écarts, champs manquants, corrections à apporter |

### Priorité 4 — Préparer les contrôles techniques

| Action | Responsable pressenti | Résultat attendu |
|---|---|---|
| Définir les informations à extraire dans les études de sol | Métier | Liste : stratigraphie, hydrogéologie, agressivité, etc. |
| Créer un tableau de restitution de la stratigraphie | Développeur prototype | Couches, profondeurs, nature du sol |
| Extraire les niveaux d’eau | Développeur prototype | EBE, EE, EC, EH, niveau chantier si disponible |
| Extraire la classe d’agressivité | Développeur prototype | XA1 / XA2 / XA3 et source documentaire |
| Préparer le contrôle de cohérence entre étude de sol, CCTP et RICT | Métier + développeur | Détection des écarts documentaires |

### Priorité 5 — Travailler sur le RICT

| Action | Responsable pressenti | Résultat attendu |
|---|---|---|
| Identifier les missions du bureau de contrôle | Développeur prototype | Liste des missions présentes |
| Créer un référentiel code / libellé des missions | Métier | Base fermée de missions |
| Synthétiser les avis suspendus et défavorables | Développeur prototype | Liste priorisée des points de vigilance |
| Vérifier le besoin d’analyse d’image pour les pictogrammes | Développeur prototype | Décision sur la méthode d’extraction |

### Priorité 6 — API et données externes

| Action | Responsable pressenti | Résultat attendu |
|---|---|---|
| Stabiliser l’adresse chantier et la géolocalisation | Développeur prototype | Coordonnées GPS fiables |
| Identifier les bonnes variables Géorisques | Louis + développeur | Variables validées dans le catalogue de données |
| Ajouter les risques naturels utiles | Développeur prototype | Inondation, argiles, carrières, radon, etc. |
| Reporter les résultats API dans la synthèse | Développeur prototype | Bloc “risques naturels et environnement” |

### Priorité 7 — Gouvernance et confidentialité

| Action | Responsable pressenti | Résultat attendu |
|---|---|---|
| Vérifier les réglages de non-entraînement des modèles | Tout utilisateur d’outil IA | Données internes non utilisées pour l’entraînement |
| Éviter l’usage de rapports sensibles dans des outils non cadrés | Équipe projet | Réduction du risque RGPD / confidentialité |
| Documenter les limites d’usage du prototype | Équipe projet | Cadre clair pour petits et moyens dossiers |
| Distinguer prototype métier et solution DSI cible | Équipe projet | Expression de besoin exploitable par la DSI |

---

## 12. Points ouverts / décisions à prendre

1. Valider définitivement la liste des champs à extraire en phase 1.
2. Récupérer et formaliser la taxonomie SMABTP des destinations d’ouvrage.
3. Définir le format cible de sortie : tableau, pré-rapport, Word, HTML ou Markdown.
4. Valider les documents pivots obligatoires pour un appel d’offres assurance.
5. Déterminer jusqu’où aller dans la synthèse RICT dès la première version.
6. Décider si les API Géorisques sont intégrées juste après l’extraction ou dans un module séparé.
7. Fixer les limites du prototype : taille maximale du dossier, nombre de documents, cas simples vs cas complexes.
8. Cadrer les règles d’utilisation des outils IA externes au regard de la confidentialité.

---

## 13. Conclusion opérationnelle

La priorité immédiate est de stabiliser l’extraction descriptive sur un cas test robuste, notamment le CHU de Rouen, avant d’élargir aux API et à l’analyse de risques.

Le livrable attendu à court terme doit être un pré-rapport structuré qui récupère les informations clés du dossier d’appel d’offres assurance : identification du projet, garanties, montants, intervenants, planning, type d’opération, destination de l’ouvrage, étude de sol, bureau de contrôle, documents manquants et premiers points de vigilance.

Une fois cette base fiable, le prototype pourra être enrichi avec :

- les API de risques naturels ;
- les contrôles de cohérence multi-documents ;
- la synthèse avancée du RICT ;
- l’analyse de risques technique ;
- la préparation de la réponse assurantielle.
