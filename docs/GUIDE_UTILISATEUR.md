# Guide de découverte — AOP

Ce document explique **simplement** ce que fait l'application AOP, sans jargon technique. Il s'adresse à toute personne du métier (souscription, gestion) qui utilise l'outil, pas aux développeurs.

## En une phrase

AOP prend un **dossier d'appel d'offres (DCE)** envoyé en vrac dans un ZIP, et aide un souscripteur à l'analyser en le triant, en vérifiant qu'il est complet, puis en en extrayant les informations utiles — le tout en français, avec toujours la source de chaque information affichée.

## Le problème que ça résout

Un DCE reçu pour une consultation (marché public de construction) arrive comme une pile de dizaines, parfois centaines, de fichiers PDF/Word/Excel mal nommés et mal rangés : plans, CCTP, études de sol, attestations d'assurance, règlement de consultation, etc. Aujourd'hui, un souscripteur doit ouvrir chaque fichier à la main pour :
1. comprendre de quoi il s'agit et le ranger dans une arborescence propre,
2. vérifier qu'aucune pièce obligatoire ne manque,
3. relever une trentaine d'informations clés (montants, garanties, adresse, équipe de maîtrise d'œuvre, dates…) pour préparer l'analyse du risque.

AOP automatise ces trois tâches tout en laissant l'humain valider chaque résultat avant de passer à la suite.

## Comment ça marche : 3 étapes, avec vous aux commandes

L'outil ne travaille jamais « en autonomie totale » : après chaque étape, il s'arrête et vous demande de vérifier/corriger avant de continuer. On appelle ça un **checkpoint**.

```
Dépôt du ZIP
     │
     ▼
Étape 1 — Tri & renommage         → vous validez le classement
     │
     ▼
Étape 2 — Vérification de complétude → vous validez ce qui manque ou non
     │
     ▼
Étape 3 — Extraction des données   → vous validez chaque valeur relevée
     │
     ▼
Dossier trié + fiche de synthèse, prêts à l'emploi
```

### Étape 0 (invisible) — Lecture des documents

Avant même l'étape 1, l'outil ouvre tous les fichiers et en extrait le texte : lecture directe pour les PDF « texte », et **OCR** (reconnaissance de texte sur image) pour les documents scannés ou les plans. Ce texte est mis en mémoire une bonne fois pour toutes, pour ne jamais avoir à relire un document deux fois.

### Étape 1 — Tri & renommage

L'outil regarde chaque fichier (son nom, son contenu, et au besoin l'avis d'une IA) et propose de le ranger dans un dossier bien organisé par catégorie (ex. `ADMIN/AAPC`, `TECH/ETUDE DE SOL`, `ASS/ATT ASS`…), avec un nom clair. Il détecte aussi les numéros de lot quand un marché en comporte plusieurs.

**Important : le dossier d'origine n'est jamais modifié.** AOP crée toujours une *copie* triée à côté — vous pouvez tout recommencer sans rien perdre.

Vous voyez le plan de classement proposé, vous pouvez corriger la catégorie ou le lot d'un fichier si l'outil s'est trompé, puis vous validez pour générer la copie triée.

### Étape 2 — Vérification de complétude

Le dossier de souscription doit contenir certaines pièces obligatoires (K-BIS, attestations décennales, étude de sol, permis de construire, etc.), regroupées en 3 phases métier (constitution du dossier / établissement du contrat / réception du chantier).

Vous cochez, dans une liste, les pièces à rechercher pour ce dossier précis. L'outil regarde alors, pour chacune :
- si elle existe en tant que fichier dédié,
- ou si elle est **noyée à l'intérieur d'un autre document** (ex. une attestation d'assurance glissée dans un gros PDF « marché signé ») — l'outil sait chercher ce genre de cas plutôt que de s'arrêter au nom des fichiers.

Chaque pièce reçoit un statut (**Présente / Partielle / Absente**) et un niveau de confiance (**Certain / Probable / À vérifier**), toujours accompagné de l'extrait de texte qui justifie la conclusion. Vous pouvez corriger n'importe quel statut à la main.

### Étape 3 — Extraction des données

L'outil relève ensuite une liste d'informations utiles à l'analyse du risque : adresse du chantier, garanties demandées (TRC, DO, RCMO…), montants, dates de début/fin, équipe de maîtrise d'œuvre, existence d'un contrôle technique, etc.

**Règle d'or : aucune valeur n'est jamais inventée.** Pour chaque donnée relevée, AOP indique :
- la valeur trouvée,
- **le document et la page** où elle a été trouvée,
- **l'extrait exact** du texte qui le prouve,
- un niveau de confiance.

Si une information est absente des documents, l'outil l'indique explicitement plutôt que de deviner. Vous pouvez aussi demander à l'outil d'approfondir la recherche sur un champ précis, ou sélectionner vous-même le document à regarder.

À la fin, l'outil rédige une courte **synthèse en français** du dossier, construite uniquement à partir des valeurs déjà validées (jamais en relisant les documents bruts au dernier moment).

## Ce que vous voyez à l'écran

- Une page d'accueil où vous déposez le ZIP du DCE et retrouvez vos dossiers déjà traités.
- Une page de suivi par dossier, avec :
  - une barre de progression et un journal en direct (utile pendant le traitement, qui peut prendre plusieurs minutes sur un gros DCE),
  - trois onglets, un par étape, qui s'ouvrent au fur et à mesure que le dossier avance,
  - en haut, une synthèse IA du dossier dès qu'elle est disponible.

## Ce qu'il faut retenir

- **Vous gardez la main** : rien n'est validé automatiquement, chaque étape attend votre accord.
- **Rien n'est perdu** : le dossier source original n'est jamais modifié ni supprimé.
- **Tout est justifié** : chaque information affichée renvoie à un document et un extrait précis — pas de « boîte noire ».
- **Rien n'est inventé** : une information introuvable est marquée comme telle, jamais devinée.
