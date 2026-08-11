# Maquettes frontend — deux directions

Propositions de refonte visuelle de l'interface AOP. Objectif : une interface plus
aboutie, qui reste professionnelle et corporate.

> **Décision : la direction B « Poste de souscription » a été retenue et implémentée.**
> Ces deux fichiers restent la référence visuelle de la refonte. Voir la section
> « Ce qui a été implémenté » en fin de document pour les écarts entre la maquette et
> l'application livrée.

Les deux maquettes sont des fichiers HTML autonomes (aucune dépendance, aucun build) :
ouvrez-les directement dans un navigateur.

| Fichier | Direction |
|---|---|
| `direction-a-cartouche.html` | **A — « Cartouche »** : l'application se lit comme une pièce écrite du DCE |
| `direction-b-console.html` | **B — « Poste de souscription »** : l'application est un instrument de travail |

Chaque fichier présente 3 écrans : accueil, dossier (extraction), audit des risques.
Le contenu affiché est réel — champs de `extraction_schema.yaml`, sections A→G de
`audit_risques_schema.yaml`, risques et données Géorisques repris du run e2e
`test-runs/campagnes/2026-07-29_e2e-cout-api-complet/grand_pic/`.

## Point de départ

L'interface actuelle est un habillage Tailwind par défaut : fond `slate-50`, barre de
progression `blue-500`, largeur `max-w-4xl`, aucune échelle typographique. Le contenu,
lui, est d'une densité d'expert — 50 champs sourcés, 30 risques référencés aux DTU et
Eurocodes. L'écart entre les deux est le vrai problème : le contenu a l'autorité d'un
rapport d'expertise, le contenant a l'allure d'un prototype.

Les deux directions traitent cet écart, par deux thèses opposées.

## Direction A — « Cartouche »

**Thèse.** AOP produit un document technique, alors il doit en avoir la tenue. Le
vocabulaire visuel est emprunté aux pièces que l'application ingère : plans, CCTP,
pièces écrites.

**Deux éléments signature :**

1. **Le cartouche** — l'identité du dossier est présentée comme le cartouche d'un plan
   d'architecte : grille de cellules réglées, intitulé en capitales fines au-dessus de
   la valeur, indice de révision daté. Les 7 chiffres clés du dossier (fichiers, pièces,
   champs, risques, zonage sismique) tiennent dans une seule bande lisible d'un coup.
2. **La cotation** — les 3 étapes du pipeline sont dessinées comme une cote de plan :
   ligne d'attache, repères verticaux aux limites, pastille d'état sur la ligne. À la
   place d'une barre de progression arrondie qui ne dit rien du métier.

**Palette.** Encre bleu de Prusse `#14263D`, papier calque `#F1F2EE` (neutre froid,
délibérément pas un crème), accent pétrole `#17414F`. Le triptyque sémantique de l'audit
est désaturé pour rester institutionnel : `#A03328` / `#9C6412` / `#45643B`.

**Typographie.** *Archivo* en display (graisse 800, chasse élargie 125 % pour les titres
— une lettre de plaque, pas de bannière marketing), *IBM Plex Sans* en courant,
*IBM Plex Mono* pour les codes techniques et les valeurs chiffrées, *IBM Plex Serif*
pour la prose d'expert de l'audit. C'est ce dernier choix qui porte le plus : un exposé
de risque décennal se lit en serif, pas en interface.

**Ce que ça donne.** Les fiches de risque sont des blocs argumentés (exposé / impact
assurabilité / recommandation), avec les référentiels en jetons monospace (`Eurocode 7 ·
NF EN 1997-1`, `DTU 13.1`). C'est la direction qui ressemble le plus à un livrable qu'on
transmet.

## Direction B — « Poste de souscription »

**Thèse.** AOP n'est pas un document, c'est un poste de travail. On y passe une heure à
recouper des valeurs, pas trois minutes à lire.

**Deux éléments signature :**

1. **Le volet de preuve permanent** — le principe directeur du projet est « aucune valeur
   inventée, citation obligatoire ». La maquette en fait un élément de structure : la
   page source, surlignée à l'endroit exact, occupe une colonne fixe à droite et ne
   quitte jamais l'écran. Sélectionner un champ à gauche change la preuve à droite. Pas
   de modale, pas d'aller-retour.
2. **La barre d'étapes segmentée** — une seule ligne découpée en 3 tronçons : on lit d'un
   coup où en est le dossier et ce qui reste, sans quitter la ligne des yeux.

**Palette.** Chrome graphite `#1A1E24` pour l'ossature (rail d'outils), plan de travail
blanc, accent ardoise `#333E70` qui sert de fil à la traçabilité — c'est la couleur du
champ sélectionné, de son jeton de source, et du filet d'ancrage dans la page PDF.

**Typographie.** *Instrument Sans* partout, *JetBrains Mono* pour les sources, pages et
mesures. Aucun serif : c'est un outil, pas un rapport.

**Ce que ça donne.** L'audit devient un tableau synoptique dense et triable (30 risques
sur un écran, avec barre de répartition critiques/vigilance/maîtrisés) plutôt qu'une
suite de fiches. Les filtres « Non trouvés (3) / Recoupés (12) / Modifiés (2) » deviennent
le point d'entrée naturel du travail de validation.

## Comment choisir

Les deux sont défendables ; elles ne servent pas le même moment d'usage.

- **A** est plus forte pour **produire et transmettre** un livrable — elle donne au
  rapport l'autorité qui manque aujourd'hui, et s'exporte naturellement en Word.
- **B** est plus forte pour **travailler et valider** — elle réduit le nombre de gestes
  pour vérifier une valeur, ce qui est l'essentiel du temps passé aux checkpoints.

Les deux ne sont pas exclusives : le volet de preuve de B peut être repris dans A, et la
prose serif de A peut servir aux écrans Synthèse/Audit de B. Si une combinaison vous
intéresse, elle se maquette rapidement à partir de ces deux fichiers.

## Ce qui a été implémenté (direction B)

- **Tokens** dans `@theme` de `frontend/src/index.css` (Tailwind v4 se configure en CSS,
  aucun `tailwind.config.js`). Toute l'ancienne palette Tailwind par défaut a disparu du
  code : plus un seul `slate-*`, `blue-*`, `green-*`, `amber-*`, `red-*`.
- **Fontes auto-hébergées** dans `frontend/public/fonts/` + `frontend/src/fonts.css`
  (sous-ensembles latin et latin-ext, ~84 ko au total). Aucun appel à un CDN : l'app doit
  fonctionner hors ligne, et l'exécutable Windows embarque `frontend/dist` en entier.
- **Vocabulaire partagé** dans `frontend/src/ui.ts` (boutons, jetons, pastilles de filtre,
  cadres). Les composants y puisent au lieu de recopier des classes — c'est cette dérive
  qui faisait paraître l'interface précédente non finie.
- **Rail d'outils** (`App.tsx`), **cartes de dossier** (`DossierList.tsx`), **barre
  d'étapes segmentée** (`DossierProgress.tsx`).
- **Volet de preuve ancré** : `CitationPreview.tsx` n'est plus une fenêtre modale mais une
  colonne `sticky` de l'écran d'extraction, qui reste en face de la liste des champs
  pendant qu'elle défile.
- **Filtres d'extraction** (Tous / Non trouvés / Recoupés / Incohérents) : le point
  d'entrée du travail de validation sur 50 champs.

### Deux écarts assumés par rapport à la maquette

1. **Les rapports sont des onglets, pas des panneaux.** La maquette les montre en onglets ;
   le code les empilait en blocs repliés au-dessus du tableau des 50 champs. Ils vivent
   désormais dans `RapportPanel.tsx`, en onglets 4 et 5 — un audit de 30 risques n'est pas
   lisible sous une autre lecture. L'onglet suivi automatiquement reste la dernière *étape*
   atteinte (1-3), jamais un onglet de rapport, qui s'ouvrirait sur un écran vide.
2. **La maquette invente des données que l'application n'a pas** à ce niveau (nom de
   l'opération, répartition des risques sur la liste des dossiers). Les cartes affichent
   les compteurs réellement disponibles, choisis selon l'étape atteinte.
