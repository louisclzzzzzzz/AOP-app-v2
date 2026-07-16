# AOP v2 — Plan d'optimisation (temps de traitement)

## Principe de coût
L'**appel LLM (chat) est la ressource rare** : rate limit + pas de parallélisme → on le traite comme un budget à dépenser avec parcimonie. Règle d'or : **dépenser le minimum d'appels LLM sur l'OCR et la classification, pour réserver le budget à l'extraction profonde** (où la précision prime).

Ce qui reste parallélisable (ne consomme pas de LLM) : hachage/dédoublonnage, extraction de texte natif PDF, classification par règles, I/O fichiers, embeddings locaux. L'OCR passe par un **endpoint séparé** (`/v1/ocr`) : concurrence modérée autorisée **avec backoff**, mais à cadencer indépendamment de la file LLM.

Deux ressources séquentielles distinctes à gérer :
1. **File LLM (chat)** : un seul worker, strictement rate-limité, priorisé.
2. **File OCR** : concurrence faible (2–4) avec backoff, ou séquentielle si le quota est serré.

---

## 1. OCR — réduire le volume à traiter

Objectif : ne jamais OCRiser deux fois, ni OCRiser ce qui est inutile. (Aucun LLM ici.)

- **Dédoublonnage par hash de contenu AVANT tout OCR.** Les DCE regorgent de doublons (`- Signature 1`, `COPIE_SAUVEGARDE`, CG répétés par lot). Un contenu identique = un seul OCR, résultat partagé. Gain typique 30–50 % sur les dossiers de référence.
- **Texte natif d'abord.** Si le PDF a une couche texte exploitable (densité de caractères suffisante par page via PyMuPDF/pdfplumber), on prend le texte directement — quasi instantané. OCR Mistral réservé aux pages scannées / à faible densité.
- **N'OCRiser que les documents utiles.** Plans déjà exclus. Étendre : la pile candidature (DC1, K-BIS, pouvoirs, attestations fiscales/sociales) n'a besoin que d'un OCR léger pour l'étape 2 ; les documents de référence de l'extraction (RC, CCAP, CCTP assurance, étude de sol) sont les seuls à traiter en profondeur.
- **Cache persistant** (déjà en place) : réutilisé par les 3 étapes, ré-OCRisable à l'unité.

---

## 2. Classification — LLM seulement si le nom est ambigu

Objectif : classer par règles déterministes, n'appeler le LLM que sur les cas réellement ambigus, et **batcher** ces cas.

### Étage 1 — Règles déterministes (zéro LLM, sur ~80 % des fichiers)
- Table de motifs (regex + mots-clés) sur le **nom de fichier** + le **dossier d'origine** → `catégorie, lot, type, confiance`.
  Exemples nets : `RC 2024.pdf`, `O.2_CCAP_Lot 2…`, `C.DC1_…`, `MLR_DCE_B.3.1…`, `…G2 PRO…`, `RICT…`. Le préfixe `C.` = candidature, `O.` = offre, `A.1.x` = plan, etc.
- Numéro de lot extrait par regex (`LOT 0?\d`, `lot\s*\d`).
- Chaque fichier reçoit un **score de confiance de règle**.

### Étage 2 — LLM uniquement sur les ambigus
Un fichier est « ambigu » si : aucun motif fort ne matche, **plusieurs** catégories matchent, ou le nom est générique (`scan001.pdf`, `document(3).pdf`, `IMG_…`, `sans titre`). Seuls ceux-là partent au LLM.
- **Batching** : regrouper plusieurs fichiers ambigus dans **un seul appel** structuré (liste en entrée → liste de classifications en sortie). Un appel pour 10 fichiers ambigus au lieu de 10 appels.
- Contexte minimal : **nom + 1ʳᵉ page OCR** (pas le document entier). Le type se lit dans l'en-tête.
- **Mistral Small** suffit ici (tâche facile) → plus rapide et n'entame pas le quota du modèle d'extraction.
- Résultats mis en cache par hash : un même fichier n'est jamais reclassé.

Résultat attendu : classification quasi gratuite en LLM (quelques appels batchés par dossier au lieu de plusieurs centaines).

---

## 3. Extraction — pousser l'analyse à fond (là où va le budget LLM)

Objectif : concentrer le budget LLM et la profondeur ici. On optimise le **nombre d'appels** et les **tokens**, pas la profondeur d'analyse.

### Ciblage
- **Routage par donnée** vers ses fichiers de référence prioritaires (RC, CCTP/CCP assurance, CCAP, étude de sol G2 AVP/PRO, PRO, PC, notice archi), défini dans `extraction_schema.yaml`. On n'extrait pas depuis les ~400 fichiers, seulement les pertinents.
- **Réduction de contexte** : ne passer au LLM que les **sections/pages pertinentes** (récupérées par mots-clés + embeddings locaux sur le texte OCR), pas le document entier. Moins de tokens = plus rapide, sans réduire la profondeur.

### Un appel riche par document (pas par champ)
- Pour chaque document de référence, **un seul appel structuré** qui extrait **tous les champs** susceptibles d'y figurer, avec pour chacun `{valeur, source (fichier+page), extrait justificatif, confiance}`. Mieux vaut 1 appel dense que 20 appels par champ.
- **Mistral Large**, température basse.

### Analyse approfondie (assumée en coût LLM)
- **Recoupement multi-sources sur les champs critiques** (montants HT/TTC, dates, garanties, niveaux, existence CT/RICT/G2) : croiser RC × CCAP × CCTP et **signaler les incohérences** plutôt que trancher au hasard.
- **Analyse profonde intra-document** de l'étape 2 (pièce noyée dans un autre document) : recherche sémantique candidate d'abord (local, sans LLM), puis **vérification LLM avec preuve** uniquement sur les passages candidats — pas sur tout le corpus.
- **Consolidation finale** : un dernier appel de synthèse par dossier qui réconcilie les valeurs et produit la fiche + niveau de sûreté.

### Ordonnancement dans la file LLM séquentielle
- Priorité : **extraction des documents de référence > vérification pièces ambiguës > classification ambiguë**.
- Traiter les documents de référence en premier ; les champs encore manquants déclenchent un élargissement ciblé seulement ensuite.

---

## 4. Orchestration des files

- **Un ordonnanceur LLM unique** : token-bucket calé sur le rate limit, retries avec backoff exponentiel + jitter, file de priorité. Aucun appel LLM concurrent.
- **File OCR séparée** : concurrence 2–4 avec backoff (à réduire si 429).
- **Pipeline en flux** : dès qu'un document est OCRisé, il entre en classification par règles ; seuls les ambigus sont mis en attente pour le batch LLM. On ne bloque pas tout le dossier.
- **Tout est caché par hash** (OCR, classification, extraction) : reprise et relance sans recoût.

---

## 5. Ordre de mise en œuvre
1. Dédoublonnage par hash + texte natif d'abord (gros gain OCR, zéro risque).
2. Classifieur à règles + score de confiance ; ne router au LLM que les ambigus, **batchés**, en Mistral Small.
3. Ordonnanceur LLM séquentiel (token-bucket + backoff + file de priorité).
4. Extraction : routage par donnée + réduction de contexte + 1 appel dense par document.
5. Recoupement multi-sources + vérification des pièces noyées + consolidation finale.

## 6. Métriques à suivre (avant/après)
- **Nombre d'appels LLM par dossier** (le KPI principal) — cible : quasi tout le budget sur l'extraction.
- Pages OCRisées vs pages totales (mesure l'effet dédup + texte natif).
- Temps par étape et temps total ; taux de 429 / backoff.
- Précision extraction inchangée ou meilleure (golden set + citations vérifiables).
