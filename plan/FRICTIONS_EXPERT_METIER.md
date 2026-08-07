# Points de friction — parcours d'un expert métier (audit UX)

Audit réalisé en naviguant l'application réelle (dossier `dce1_marly.zip`, un DCE terminé
avec ses 3 étapes) puis en vérifiant chaque observation dans le code (frontend
`frontend/src/`, backend `backend/app/`) pour confirmer les causes et éviter les faux
positifs. Objectif : lister ce qu'un souscripteur / gestionnaire de sinistres SMABTP (pas un
développeur) rencontrerait comme points de blocage, d'incompréhension ou de perte de
confiance en utilisant l'outil, pour prioriser les prochaines améliorations.

Chaque point indique où le vérifier dans le code (`fichier:ligne`) quand c'est pertinent.

---

## 1. Accueil / liste des dossiers

- **Pas de recherche, filtre ni tri.** `DossierList.tsx` affiche la liste brute renvoyée par
  l'API, sans champ de recherche par nom de projet/commune, ni filtre par statut (ex.
  « dossiers en attente de ma validation »), ni tri. Avec 7 dossiers ça passe ; avec les
  dizaines de DCE qu'un souscripteur traite par mois, la liste devient vite inexploitable.

- **Pas de suppression de dossier.** Aucune route `DELETE` n'existe côté backend
  (`backend/app/api/dossiers.py`) et aucun bouton côté UI. Un dossier test, un mauvais
  upload ou un doublon reste dans la liste indéfiniment.

- **Les doublons d'upload ne sont pas détectés.** `upload_dossier`
  (`backend/app/api/dossiers.py:119-139`) crée systématiquement un nouveau dossier, sans
  comparer au contenu déjà présent. C'est visible dans les données actuelles : `dce1_marly.zip`
  et `copie_dce_2026_BASIC_ACCORD CADRE.zip` apparaissent chacun deux fois dans la liste, avec
  un traitement complet (donc un coût OCR/LLM) refait à l'identique à chaque fois.

- **Glisser-déposer un mauvais type de fichier échoue silencieusement.** Dans
  `UploadDropzone.tsx:11-22`, `handleDrop` ignore purement et simplement tout fichier qui ne
  finit pas par `.zip` — aucun message d'erreur. À l'inverse, passer par « parcourez vos
  fichiers » sans respecter l'extension déclenche un appel réseau qui remonte l'erreur serveur
  (`App.tsx:29-33`, message rouge affiché). Deux chemins pour la même erreur, deux
  comportements différents ; le glisser-déposer (le geste le plus naturel) est celui qui ne
  dit rien.

- **Échec de chargement de la liste = liste vide, sans distinction.** `App.tsx:15` :
  `listDossiers().then(setDossiers).catch(() => {})`. Si le backend est indisponible au
  chargement, l'utilisateur voit « Aucun dossier traité pour l'instant » — un message
  identique à l'état réellement vide, aucun indice qu'il s'agit d'une panne.

- **Nom de fichier tronqué sans info-bulle sur la liste d'accueil**
  (`DossierList.tsx:33`, classe `truncate` sans `title=`), contrairement à d'autres endroits
  de l'app qui, eux, portent un `title=` (voir §2).

---

## 2. Traçabilité transversale (touche les 3 étapes)

Le principe affiché en page d'accueil du projet est « la précision et la traçabilité
priment toujours sur la vitesse ». Plusieurs éléments d'UI vont à l'encontre de ce principe
en pratique :

- **Les colonnes « Justification » et « Localisation » sont tronquées et ne s'affichent en
  entier qu'au survol (info-bulle navigateur native), sans aucun indice visuel qu'on peut
  survoler.** Rien ne l'indique (pas de soulignement, pas de curseur d'aide, pas d'icône
  « i »). Dans `CompletenessChecklist.tsx:328` et `ReorganizationPlan.tsx:223`, le texte
  complet est bien dans l'attribut `title`, donc techniquement accessible — mais un expert qui
  scanne 15 pièces ou 30 champs à la suite ne va pas deviner qu'il faut immobiliser la souris
  sur chaque cellule tronquée pour lire le raisonnement de l'IA. Ce mécanisme ne fonctionne pas
  non plus sur écran tactile, et n'apparaît pas dans le rapport téléchargé.

- **La liste « Localisation » d'une pièce partiellement/pleinement trouvée, une fois dépliée
  via « + N autres », montre des chemins tous tronqués à la même largeur** (`max-w-[11rem]` /
  ~15-20 caractères visibles, `CompletenessChecklist.tsx:59-68`). Exemple observé sur la pièce
  « CCTP des entreprises… » : les 26 sources listées affichent quasiment toutes
  `CONSERVATOIRE CCTP TRAVA...`, visuellement indiscernables les unes des autres. Il faut
  survoler chaque ligne une par une pour savoir laquelle regarder.

- **Le badge « ⚠ Incohérence » (étape 3, recoupement de sources) ne montre les valeurs en
  conflit qu'au survol**, jamais affichées en clair
  (`ExtractionSheet.tsx:366-376` : `title={... value (filename) vs value (filename) ...}`
  uniquement si `incoherent`). Un expert qui doit justement arbitrer entre deux dates ou deux
  montants contradictoires ne voit d'abord qu'une pastille rouge, sans le détail — c'est
  exactement le cas où l'info devrait être visible sans action supplémentaire.

- **Bug d'encodage réel dans les noms de fichiers**, pas seulement un artefact d'affichage.
  Vérifié au niveau octet dans le rapport JSON exporté : `LOT N\xe2\x96\x91 1` — le caractère
  stocké est U+2591 « ░ » (bloc de trame) à la place du signe degré « ° » attendu
  (« LOT N°1 »). Ça se voit partout où les chemins d'origine sont affichés : tableau
  d'inventaire, colonne « Sources » de l'étape 3, arborescence de l'étape 1. Cause probable :
  mauvais décodage de page de code (cp850/cp437 vs Windows-1252) lors de la décompression
  récursive. Ce n'est pas cosmétique : ça donne l'impression d'un outil buggé sur un dossier
  par ailleurs entièrement traité avec succès.

- **Du jargon interne apparaît dans les justifications destinées au métier.** Exemple exact
  observé : *« Aucun document classé dans TECH/ARRETE PC et cette pièce n'est pas recherchée
  ailleurs (peut_etre_inclus_dans_autre=false). »* — le nom de variable Python
  `peut_etre_inclus_dans_autre=false` (`backend/app/classify/engine.py:256-262`) fuite tel
  quel dans une phrase censée être lue par un souscripteur.

- **Le menu déroulant de correction de catégorie (étape 1, checkpoint) affiche les chemins
  bruts de la taxonomie** (`ReorganizationPlan.tsx:186-190` : `<option value={c.path}>{c.path}</option>`,
  donc des libellés comme `TECH/ARRETE PC` ou `ASS/LISTE INTERVENANTS`) plutôt qu'un libellé
  humain. Utilisable par quelqu'un qui connaît déjà la taxonomie interne, moins évident pour
  un nouvel utilisateur.

- **Aucune légende pour les codes de missions du bureau de contrôle** (« L, LE, PS, Hand, Brd,
  Av, GTB, HYS, PV, VTIE, Att HAND, TH » affiché tel quel en étape 3). Même le schéma de
  configuration ne les définit pas (`backend/config/extraction_schema.yaml:170` :
  `"Liste des missions (L, LE, PS, …)"`). Ce sont des codes métier standards mais tout
  utilisateur pas encore rodé à cette nomenclature n'a aucun moyen de les décoder dans l'outil.

---

## 3. Étape 1 — Classification & réorganisation

- **Les noms de fichiers normalisés peuvent devenir trompeurs ou quasi-identiques entre deux
  lots différents.** `naming.py:13-19` tronque le nom à `max_len=50` caractères *avant* de
  vérifier les doublons, et `dedupe_target_filename` (`naming.py:41-52`) ne compare qu'au sein
  d'un même dossier cible. Résultat observé concrètement sur le dossier test : les fichiers du
  LOT 1 et du LOT 2 (deux actes d'engagement différents) sont tous deux renommés
  `..._AE-LOT-N-1-ET-TRC-ET-AE-LO.docx` (mot coupé en plein milieu, `LO` au lieu de `LOT`), et
  dans LOT 3 deux fichiers distincts ne se distinguent que par `-LOT-2.docx` vs `-LOT.docx`.
  Un expert qui relit l'arborescence proposée ne peut pas deviner quel fichier est lequel sans
  ouvrir chacun.

- **Une fois l'étape 1 appliquée, impossible de revenir corriger un classement — même si le
  moteur le permettrait techniquement.** Le backend est conçu pour être « idempotent /
  rebuildable » (réapplication possible, `reorg.py`), mais le frontend verrouille
  définitivement l'écran d'édition dès que `status` dépasse `reorganized`
  (`statusFlow.ts:5-30`, comparaison `isAtOrAfter` à sens unique, utilisée dans
  `ReorganizationPlan.tsx:44-49` et `92`). Si l'expert découvre une erreur de classement après
  coup (nouveau document trouvé, catégorie mal choisie), il n'y a aucun bouton « reclasser » —
  et aucune route backend de type reopen/rollback n'existe non plus
  (vérifié : aucune route `reopen`/`revert` dans `backend/app/api/`).

- **Aucun moyen d'ouvrir/prévisualiser un document depuis l'arborescence.** `OrganizedTree.tsx`
  affiche uniquement des noms de fichiers en texte, sans lien pour ouvrir le PDF/DOCX
  correspondant — il faut aller chercher le fichier ailleurs (Explorateur/Finder) pour
  vérifier son contenu.

---

## 4. Étape 2 — Complétude

- Voir §2 pour la troncature de la colonne Localisation, qui touche particulièrement cette
  étape (jusqu'à 26 sources pour une seule pièce).

- **Pour les pièces jugées « Absente/Probable » sur la base de mots-clés trouvés mais non
  confirmés, les documents concernés ne sont pas nommés.** Exemple observé : *« 3 document(s)
  contenaient les mots-clés recherchés mais le LLM n'a confirmé la présence dans aucun d'eux »*
  — sans dire lesquels. L'expert qui veut vérifier lui-même le jugement de l'IA doit rechercher
  à l'aveugle dans les 80 fichiers du dossier plutôt que d'ouvrir directement les 3 documents
  suspects.

- **Profondeur de justification inégale.** Certaines lignes citent un extrait du document
  entre guillemets avec sa source (très utile pour vérifier rapidement) ; d'autres ne donnent
  qu'une phrase générique sans citation. Rien ne distingue visuellement les deux cas — l'expert
  ne sait pas à l'avance s'il peut faire confiance à la justification affichée ou s'il doit
  aller vérifier lui-même.

---

## 5. Étape 3 — Extraction

- **Les sources listées pour chaque champ extrait sont du texte brut, non cliquable, sans
  numéro de page ni extrait.** (`ExtractionSheet.tsx:355-361` : simple concaténation de noms de
  fichiers.) Impossible de vérifier une valeur extraite sans quitter l'application et rouvrir
  le document soi-même — alors que la citation est justement présentée comme un principe
  directeur du projet.

- **Le tableau extraction est modifiable uniquement pendant la fenêtre `extraction_review`**,
  puis verrouillé pour toujours dès validation (`ExtractionSheet.tsx:270,332`) — cohérent avec
  le principe de checkpoint humain, mais sans porte de sortie si une erreur est repérée après
  coup (même limitation qu'en étape 1, voir §3 : aucune route de réouverture côté backend).

- **Le compteur « Non analysables » du tableau de bord mélange des cas anodins et des cas
  potentiellement critiques**, sans le dire. `backend/app/ingestion/classify_extension.py:18-45`
  range dans la même case :
  - des cas sans enjeu : plans identifiés par nom de fichier (`_PLAN_FILENAME_REASON`,
    `inventory.py:25-27`), fichiers de signature électronique dématérialisée
    (`.cle/.cry/.iv/.pli/.xml`), fichiers système ;
  - et des cas qui *devraient* attirer l'attention de l'expert : **archive protégée par mot
    de passe ou corrompue** (`inventory.py:79`) ou **extension non prise en charge**
    (`classify_extension.py:45`) — deux cas où un document potentiellement obligatoire (ex.
    une attestation dans un sous-zip protégé) peut être invisible pour tout le pipeline sans
    qu'aucune alerte distincte ne le signale. Sur le dossier testé, 22 fichiers sont comptés
    en « Non analysables » sans que le tableau de bord ne distingue lesquels sont de simples
    plans (sans risque) et lesquels pourraient cacher une pièce manquante.

---

## Pistes de priorisation

**Gains rapides, fort impact perçu :**
1. Ajouter un indice visuel (icône, soulignement pointillé) sur toute cellule tronquée qui a
   un `title=` — sinon la fonctionnalité de traçabilité existe mais reste invisible.
2. Corriger le décodage de nom de fichier qui produit « N░ » à la place de « N° » — bug
   d'encodage concret, déjà localisable (pipeline de décompression).
3. Afficher un message d'erreur au glisser-déposer d'un fichier non-`.zip` (actuellement
   silencieux).
4. Distinguer visuellement, dans le compteur « Non analysables », les archives
   protégées/corrompues et extensions non supportées (risque réel) du reste (plans, fichiers
   système — sans risque).

**Plus structurant :**
5. Permettre de rouvrir/recorriger une étape déjà validée (au moins l'étape 1, où le backend
   le permet déjà techniquement).
6. Ajouter recherche/filtre/tri + suppression sur la liste des dossiers, et une détection de
   doublon à l'upload (évite de repayer un traitement OCR/LLM identique).
7. Rendre les sources de l'étape 3 cliquables vers un aperçu du document (page/extrait), pour
   tenir la promesse de traçabilité de bout en bout.
8. Revoir la troncature à largeur fixe des noms de fichiers normalisés (étape 1) pour éviter
   les collisions visuelles entre lots différents.
