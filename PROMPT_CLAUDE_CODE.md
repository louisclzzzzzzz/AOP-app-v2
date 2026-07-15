# Prompt pour Claude Code — Implémentation AOP v2

> Copie-colle ce prompt dans Claude Code, à la racine d'un dossier de projet vide, avec `PLAN.md`, `arborescence.md`, `donnees_de_ref.md` et `liste_piece.md` présents dans le dossier.

---

Tu vas construire **AOP v2**, une application web locale d'aide à l'analyse de DCE (Dossiers de Consultation des Entreprises) pour l'underwriting assurance construction (SMABTP, appels d'offres publics).

## Documents de référence (lis-les EN ENTIER avant de coder)
- `PLAN.md` — spécification complète et source de vérité. Respecte-la scrupuleusement. En cas de doute, relis-la avant d'improviser.
- `arborescence.md` — 6 dossiers réels triés par l'expert métier. C'est ton **golden set** pour les règles de classement de l'étape 1. (Fichier encodé en UTF-16 ; lis-le avec le bon encodage.)
- `donnees_de_ref.md` — données à extraire (étape 3, Feuil2) + tableau de référence RCMO/TRC (Feuil1).
- `liste_piece.md` — checklist des pièces (étape 2), en 3 phases.

## Principe directeur ABSOLU
**La précision et la qualité des résultats priment TOUJOURS sur le temps de traitement et le coût API.** À chaque arbitrage, choisis la solution la plus fiable : OCR systématique sur documents scannés, multi-passes de recoupement, vérification LLM avec citation obligatoire. Ne prends jamais un raccourci qui dégrade la fiabilité pour aller plus vite.

## Contraintes non négociables
1. **IA = API Mistral uniquement.** OCR via `mistral-ocr-latest` (`/v1/ocr`, exploite les scores de confiance et bounding boxes). LLM via `mistral-large-latest` (Structured Outputs pour la classification et l'extraction ; multimodal pour les documents à forte composante image). Clé via `MISTRAL_API_KEY` (`.env`). Épingle des versions datées et centralise-les dans `config/models.yaml`.
2. **App web locale.** Backend Python/FastAPI (REST + WebSocket pour le suivi live), frontend React + Vite + TypeScript + Tailwind. Lancement en une commande, sur `localhost`. Pas de dépendance cloud hors API Mistral.
3. **Étape 1 = copie triée.** Ne modifie, ne renomme et ne déplace JAMAIS les fichiers source. Génère un nouveau dossier organisé (`workspace/<id>/organized/`) par **copie**. La source (`workspace/<id>/source/`) est immuable.
4. **Checkpoints humains** entre les 3 étapes : chaque étape est bloquée tant que la précédente n'est pas validée dans l'UI. L'utilisateur peut corriger les résultats de chaque étape avant de continuer.
5. **Traçabilité totale.** Toute décision (classement, présence de pièce, valeur extraite) stocke : source (fichier + page), extrait justificatif, score de confiance, modèle + version, horodatage. Aucune valeur affirmée sans preuve ; une donnée absente = `null` explicite, jamais une invention.

## Ce que tu dois livrer
Le projet complet décrit au §10 de `PLAN.md`, avec les fonctionnalités des §3 à §8. En particulier :

### Socle (étape 0)
- Upload drag-drop d'un `.zip`, dézippage **récursif** (gère les zips imbriqués), inventaire de chaque fichier (id, hash, taille, extension, chemin d'origine).
- Extraction texte : PDF natif en direct + **OCR Mistral systématique** sur scans/images/PDF plans ; DOCX/DOC convertis. **Cache OCR persistant** (SQLite + `.md`) réutilisé par toutes les étapes — n'OCRise jamais deux fois le même document.
- Fichiers de dépôt dématérialisé (`.cle`, `.cry`, `.iv`, `.pli`, `.xml`) marqués « non analysable » mais conservés.
- Suivi de progression **live via WebSocket** : global + par document (dézip → OCR → classification), avec compteurs.

### Étape 1 — Réorganisation
- Classifieur combinant **3 signaux** : nom de fichier (regex/mots-clés), contenu OCR, et LLM classifieur à sortie structurée. Il renvoie `{catégorie, lot, type_document, nom_normalisé, confiance, justification}`.
- Arborescence cible et règles selon §4.2 de `PLAN.md`. Config dans `config/taxonomy.yaml`.
- Gestion multi-lots (sous-dossiers par lot quand le document porte un n° de lot).
- UI : **plan de réorganisation éditable** (arborescence cible + renommages + confiance + justification), l'utilisateur valide/corrige/re-catégorise, puis bouton « Appliquer la copie triée » → copie réelle + rapport (JSON + lisible).
- **Valide-toi sur le golden set** : ta classification des 6 dossiers de `arborescence.md` doit reproduire l'arborescence de l'expert. Écris des tests correspondants.

### Étape 2 — Complétude
- Charge `config/pieces_checklist.yaml` (que tu génères à partir des 3 phases de `liste_piece.md` / §5.2 de `PLAN.md`, avec alias et indices pour chaque pièce). UI : cases à cocher **groupées par phase**.
- **Analyse profonde en 3 couches** (§5.3) : correspondance par fichier → **correspondance intra-document** (une pièce peut être noyée dans un autre document : recherche sémantique/mots-clés sur TOUT le contenu OCR) → vérification LLM avec **preuve citée**.
- Cas spéciaux : attestations décennale **par lot** (couverture lot par lot) et **valables à la date de la DOC** ; DOC signée avec **fallback** sur 1er OS signé ; K-BIS et pièces d'identité **inclus dans** la demande d'assurance.
- Chaque pièce : statut (`Présente/Partielle/Absente`) + **niveau de sûreté** (`Certain/Probable/À vérifier/Absent`) consolidé (confiance OCR + force de correspondance + confiance LLM) + localisation + extrait de preuve cliquable.
- Écris un test « pièce noyée dans un autre document » (couche 2).

### Étape 3 — Extraction
- Schéma `config/extraction_schema.yaml` généré depuis `donnees_de_ref.md` (Feuil2 : groupe principal + informations complémentaires à vérifier).
- Extraction via Structured Outputs Mistral, **routée** vers les fichiers de référence pertinents (RC, CCTP/CCP assurance, CCAP, étude de sol, PRO, PC, notice archi…), élargie si absent.
- Chaque valeur : `{valeur, source (fichier+page), extrait justificatif, confiance}`. **Multi-passes de recoupement** pour montants/dates/garanties, avec signalement des incohérences entre RC/CCAP/CCTP.
- Normalisation (montants HT/TTC, dates ISO). Optionnel/à confirmer : pré-calcul du niveau RCMO/TRC via le tableau Feuil1 de `donnees_de_ref.md` (mets-le derrière un flag, ne l'impose pas).

### Exports & UI
- Exports : dossier trié (zip), rapports JSON, **Excel** (complétude + données) et **PDF** de synthèse.
- UI complète selon §8 de `PLAN.md` : accueil + liste des dossiers (SQLite), suivi live, 3 écrans d'étape avec sources cliquables (fichier + page) et confiance affichée partout.

## Méthode de travail attendue
1. Commence par **lire les 4 documents de référence en entier**, puis propose-moi une courte confirmation de ta compréhension et l'ordre de build (suis les phases du §11 de `PLAN.md`).
2. Construis **phase par phase**, en livrant à chaque phase quelque chose de lançable et testé. Ne passe pas à la phase suivante sans tests verts.
3. **Teste sur les données réelles** : le golden set de `arborescence.md` pour l'étape 1 ; des cas « pièce noyée » pour l'étape 2 ; des documents avec citations vérifiables pour l'étape 3.
4. Gère proprement erreurs et reprises : un dossier peut être relancé à n'importe quelle étape, l'OCR est mis en cache, rien n'est perdu (tout fichier non classé va dans `AUTRES`).
5. Fournis un `README.md` : installation, `.env.example` (`MISTRAL_API_KEY`), commande de lancement unique, et comment brancher/éditer les fichiers de `config/`.
6. Ne code pas de secrets en dur. Paramètres et versions de modèles dans `config/`.

## Qualité
- Code typé (Python type hints, TypeScript strict), modulaire selon l'arborescence du §10.
- Tests unitaires + tests d'intégration sur le golden set. Température LLM basse pour classification/extraction.
- Priorité constante : **fiabilité et traçabilité avant vitesse**.

Commence maintenant par lire les documents de référence et me confirmer ta compréhension + le plan de build phasé.
