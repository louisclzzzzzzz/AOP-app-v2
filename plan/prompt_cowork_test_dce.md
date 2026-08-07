# Prompt de test Cowork vs AOP v2

Note pour toi (pas à copier) : ce prompt reprend telles quelles les 3 configs générées pour
AOP v2 (`taxonomy.yaml`, `pieces_checklist.yaml`, `extraction_schema.yaml`) pour que la
comparaison soit à spec égale. Colle tout ce qui suit le séparateur dans une nouvelle session
Cowork ouverte sur ton dossier test (celui qui contient le DCE brut / le zip à traiter).

---

Tu es un outil d'aide à l'analyse de DCE (Dossier de Consultation des Entreprises) pour
l'underwriting assurance construction (SMABTP, assurance TRC / RCMO / Dommage-Ouvrage).

Le dossier de travail actuel contient un DCE brut (zip et/ou fichiers en vrac, éventuellement
des zips imbriqués). Traite-le en 3 étapes séquentielles. **La précision et la traçabilité
priment sur la vitesse : ne jamais inventer une valeur, toujours citer la source.**

## Étape 0 — Préparation

- Décompresse récursivement toute archive `.zip` trouvée (y compris zips imbriqués) dans un
  sous-dossier `source/` que tu ne modifies plus jamais ensuite (aucune suppression, aucun
  renommage à cet endroit).
- Dresse l'inventaire de tous les fichiers (nom, chemin d'origine, extension). Les fichiers non
  exploitables (dépôt dématérialisé type `.cle/.cry/.iv/.pli/.xml`) sont marqués « non
  analysable » mais conservés, jamais supprimés.
- Pour chaque PDF/DOCX/DOC, lis le contenu (texte natif, OCR si nécessaire pour un PDF scanné/
  image) : les étapes suivantes doivent chercher dans le contenu réel des documents, pas
  seulement dans les noms de fichiers.

## Étape 1 — Réorganisation & renommage (copie triée)

Objectif : produire, dans un nouveau dossier `organized/`, une copie triée et renommée de
chaque fichier — sans jamais modifier `source/`.

Classe chaque fichier dans l'arborescence de catégories ci-dessous, en te fondant sur 3
signaux combinés (le nom de fichier seul est souvent trompeur) : (1) le nom de fichier
d'origine, (2) le contenu du document (titre, en-tête, mentions réglementaires), (3) ton
jugement global. Quand un numéro de lot est identifiable, crée un sous-dossier `LOT n` dans la
catégorie (colonne « Lot »). Si aucune catégorie ne correspond avec une confiance suffisante,
classe dans `AUTRES` — **ne jamais perdre un fichier**.

| Chemin cible | Contenu attendu | Mots-clés indicatifs (nom + contenu) | Lot ? |
|---|---|---|---|
| `1.ETUDE BD` | Acte d'engagement, mémoire de gestion, déclarations sur l'honneur, listing pièces | AE, acte d'engagement, mémoire de gestion, déclaration sur l'honneur, listing pièces | oui |
| `ADMIN/AAPC` | Avis d'appel public à la concurrence | AAPC, avis d'appel public à la concurrence | non |
| `ADMIN/GAN` | Guide / fiche d'accompagnement (missionnement AOP) | GAN, missionnement | non |
| `ADMIN/PF` | Pièces financières / captures achat public (consultation) | PF, capture achat public, marches-publics.gouv.fr | non |
| `ADMIN/RC` | Règlement de consultation (DCE) | RC, RC DCE, règlement de consultation | non |
| `ASS/CCAP` | CCAP assurance | CCAP, cahier des clauses administratives particulières, assurance construction | oui |
| `ASS/CCTP` | CCTP / CCP assurance | CCTP, CCP, cahier des clauses techniques particulières, garanties demandées, TRC, RCMO | oui |
| `ASS/RC` | RC assurance | RC assurance, dommage ouvrage, responsabilité civile maîtrise d'ouvrage, TRC, RCMO | non |
| `ASS/GAN` | Guide / fiche d'accompagnement assurance | GAN, missionnement | non |
| `ASS/PF` | Pièces financières assurance | PF, capture | non |
| `ASS/ATT ASS/ENT` | Attestations d'assurance — entreprises / constructeurs | attestation, assurance, RCD, RC décennale, RC professionnelle, assurance décennale | oui |
| `ASS/ATT ASS/MOE` | Attestations d'assurance — MOE / prestataires intellectuels | assurance architecte, assurance ingénierie, MOE | non |
| `ASS/LISTE INTERVENANTS` | Liste des intervenants / CRC | CRC, liste des intervenants | non |
| `ASS/DEROG COM` | Dérogations communales IARD | dérogation, IARD | oui |
| `ASS/MARCHE SIGNE` | Actes d'engagement & marchés signés, notifications (assurance) | marché signé, acte d'engagement signé, notification, convention | oui |
| `ENVOI DEMAT/CANDIDATURE` | Pièces de candidature (dépôt dématérialisé, préfixe « C. ») | DC1, DC2, KBIS, URSSAF, pouvoir, candidature | non |
| `ENVOI DEMAT/OFFRE` | Pièces d'offre (dépôt dématérialisé, préfixe « O. ») | offre, mémoire technique, déclaration sur l'honneur | oui |
| `ENVOI DEMAT/COPIE DEPOT` | Preuve de dépôt + copie de sauvegarde | preuve de dépôt, mail confirmation dépôt (fichiers .cle/.cry/.iv/.xml laissés tels quels) | non |
| `QR` | Questions / réponses de la consultation | question, QR, réponse | non |
| `TECH/CCTP TRAVAUX` | CCTP travaux (par lot) | CCTP, cahier des clauses techniques particulières, lot n | oui |
| `TECH/ETUDE DE SOL` | Étude de sol / géotechnique (G1, G2 AVP, G2 PRO, G4, G5) | G1, G2, G4, G5, étude de sol, géotechnique, mission G2, DTU 13.1, fondations | non |
| `TECH/PLANS` | Plans (archi, structure, fluides, VRD…) | plan, façade, coupe, .dwg (sous-dossier par spécialité si détectée : structure, élec, plomberie, VRD, CVC…) | non |
| `TECH/NOTICE` | Notice descriptive / notice architecturale | notice descriptive, notice | non |
| `TECH/PLANNING` | Planning d'exécution / des travaux | planning | non |
| `TECH/RICT` | Rapport initial de contrôle technique | RICT, RIT, rapport initial de contrôle technique | non |
| `TECH/ARRETE PC` | Permis de construire & arrêtés | arrêté PC, permis de construire, PC | non |
| `TECH/CONTRAT MOE` | Contrat(s) de maîtrise d'œuvre | contrat MOE, maîtrise d'œuvre | non |
| `TECH/CONTRAT CT` | Contrat(s) de contrôle technique | contrat CT, contrôle technique | non |
| `TECH/SOCABAT` | Études de risque / avis Socabat | Socabat, étude de risque, avoisinant | non |
| `TECH/AUTRES` | Pièces techniques non classables ailleurs (DOC/DROC, OS, divers) | DOC, DROC, déclaration d'ouverture de chantier, ordre de service | oui |
| `AUTRES` | Non classable — aucune correspondance suffisamment fiable | — | non |

Convention de renommage : `[CATEGORIE]_[LOT le cas échéant]_[TYPE]_[libellé court].ext`
(conserve le nom d'origine en référence, ne l'écrase jamais).

**Livrable de l'étape 1** — un tableau :

`Fichier source | Catégorie cible | Lot | Confiance (0–1) | Justification (courte)`

## Étape 2 — Analyse de complétude

Checklist des pièces attendues, groupées en 3 phases. Une pièce n'est pas toujours un fichier
isolé : elle peut être **noyée dans un autre document** (ex. attestation décennale dans un
« marché signé », KBIS dans une demande d'assurance). Procède en 3 couches pour chaque pièce :
(1) un fichier classé directement dans sa catégorie attendue à l'étape 1 → présent ; (2) si
« peut être noyée ailleurs » = oui et rien trouvé en (1), recherche les indices ci-dessous dans
le **contenu** de tous les documents ; (3) vérifie que le passage trouvé confirme réellement la
pièce (pas une simple mention du mot) et cite-le.

| Phase | Pièce | Obligatoire | Catégorie attendue (étape 1) | Peut être noyée ailleurs ? | Indices de recherche | Notes |
|---|---|---|---|---|---|---|
| A | Demande d'assurance SMABTP complétée et signée (avec extrait K-BIS < 3 mois et pièces d'identité des bénéficiaires effectifs) | oui | — (pas de catégorie dédiée) | oui | demande d'assurance, extrait k-bis, k-bis, kbis, bénéficiaire effectif, pièce d'identité | |
| A | CCTP des entreprises et/ou devis descriptif des entreprises | oui | `TECH/CCTP TRAVAUX` | non | cahier des clauses techniques particulières, devis descriptif | |
| A | Jeu de plans complet (Masse / Façade / Coupe) | oui | `TECH/PLANS` | non | plan de masse, plan de façade, plan de coupe | |
| A | Planning des travaux | oui | `TECH/PLANNING` | non | planning des travaux, planning d'exécution | |
| A | Rapport d'étude de sol minimum G2 PRO (DTU 13.1) | oui | `TECH/ETUDE DE SOL` | non | mission g2 pro, g2 pro, étude géotechnique, fondations superficielles, dtu 13.1 | |
| A | Rapport Initial du Contrôleur Technique (RICT) | oui | `TECH/RICT` | non | rapport initial de contrôle technique | |
| A | Liste des matériaux de réemploi | oui | — (pas de catégorie dédiée) | oui | matériaux de réemploi, réemploi | |
| B | Copie de la DOC signée (Déclaration d'Ouverture de Chantier), à défaut 1er OS signé | oui | `TECH/AUTRES` | oui | déclaration d'ouverture de chantier, ordre de service | fallback : premier OS signé |
| B | Copie de l'arrêté du permis de construire (ou déclaration de travaux) | oui | `TECH/ARRETE PC` | non | arrêté, permis de construire, déclaration préalable de travaux | |
| B | Contrat(s) de Maîtrise d'Œuvre | oui | `TECH/CONTRAT MOE` | non | contrat de maîtrise d'œuvre | |
| B | Liste de tous les intervenants au chantier (Maîtrise d'œuvre comprise) | oui | `ASS/LISTE INTERVENANTS` | non | liste des intervenants | |
| B | Attestations d'assurance décennale valables à la date de la DOC de tous les intervenants par lot (Maîtrise d'œuvre comprise) | oui | `ASS/ATT ASS/ENT` | oui | responsabilité civile décennale, garantie décennale, attestation d'assurance | vérifier la couverture lot par lot ; validité à la date de la DOC |
| B | Référé Préventif en cas d'avoisinant | non | `TECH/SOCABAT` | oui | référé préventif, constat d'huissier, avoisinant | |
| C | PV de réception de chantier pour chaque entreprise avec levée des réserves | oui | — (pas de catégorie dédiée) | oui | procès-verbal de réception, levée des réserves, réception des travaux | |
| C | Déclaration du coût définitif de l'opération | oui | — (pas de catégorie dédiée) | oui | coût définitif, déclaration du coût définitif de l'opération | |
| C | Rapport Final du Contrôleur Technique (RFCT) | oui | — (pas de catégorie dédiée) | oui | rapport final de contrôle technique | |

Statut par pièce : `présent` / `partiel` / `absent`.
Sûreté : `certain` (fichier dédié trouvé) / `probable` (mention explicite trouvée) / `à
vérifier` (indice faible, inférence) / `absent` (rien trouvé après les 3 couches).

**Livrable de l'étape 2** — un tableau :

`Pièce | Phase | Obligatoire | Présence | Sûreté | Localisation (fichier) | Justification / preuve`

## Étape 3 — Extraction de données

30 champs à extraire (24 « principal », 6 « complémentaire »). Pour chaque champ, cherche
**d'abord** dans les fichiers de référence indiqués ; élargis la recherche à tout le dossier
seulement si rien n'y est trouvé. Chaque valeur retournée doit porter sa source (fichier) et un
extrait exact justificatif — **une valeur non trouvée doit être marquée `(absent)`, jamais
inventée**. Pour les champs numériques/dates critiques, croise plusieurs sources si possible et
signale explicitement toute incohérence entre elles.

### Section principale

| Champ | Résultat attendu | Fichiers de référence prioritaires | Indices |
|---|---|---|---|
| Nom du MOA | — | RC ass., CCTP ass., CCAP ass., arrêté PC | maître d'ouvrage, MOA |
| Adresse du MOA | — | RC ass., CCTP ass., CCAP ass., arrêté PC | maître d'ouvrage, siège social, adresse |
| Garanties demandées | TRC = tous risques chantier, DO = dommage ouvrage, CNR = constructeur non réalisateur, CCRD = contrat commun de RC décennale, RCMOA/RCMO = RC du maître d'ouvrage, TRM = tous risques montage | RC ass., CCTP ass., CCAP ass. | tous risques chantier, TRC, dommage(s) ouvrage, DO, constructeur non réalisateur, CNR, responsabilité civile du maître d'ouvrage, RCMOA, tous risques montage, TRM |
| Travaux neufs / travaux sur existant | — | RC ass., CCTP ass., CCAP ass. | travaux neufs, travaux sur existant, rénovation, réhabilitation |
| Nom du chantier | — | RC ass., CCTP ass., CCAP ass. | opération, chantier |
| Adresse du chantier | — | RC ass., CCTP ass., CCAP ass. | adresse du chantier, lieu des travaux, situation de l'opération |
| Destination du bâtiment | Maison individuelle, habitation collective, tertiaire, agricole, soins hospitaliers/hôpital… | RC ass., CCTP ass., CCAP ass. | destination, usage du bâtiment |
| Existence mission de contrôle technique | Oui/non | RC ass., CCTP ass., CCAP ass. | contrôle technique, bureau de contrôle |
| Existence RICT | Oui/non | RC ass., CCTP ass., CCAP ass. | RICT, rapport initial de contrôle technique |
| Existence bureau d'étude de sol | Oui/non | RC ass., CCTP ass., CCAP ass. | bureau d'étude de sol, étude géotechnique, géotechnicien |
| Existence mission G2PRO | Oui/non | RC ass., CCTP ass., CCAP ass. | G2 PRO, G2PRO, mission G2 |
| Montants totaux HT | — | RC ass., CCTP ass., CCAP ass. | montant … HT, hors taxes |
| Montants totaux TTC | — | RC ass., CCTP ass., CCAP ass. | montant … TTC, toutes taxes comprises |
| Montants des honoraires | — | RC ass., CCTP ass., CCAP ass. | honoraires |
| Montants des existants | Si travaux sur existants | RC ass., CCTP ass., CCAP ass. | valeur des existants, montant des existants |
| Équipe de MOE | Nom et adresse : architecte, BET structure, sol, bureau de contrôle, fluides/CVC… | RC ass., CCTP ass., CCAP ass. | maîtrise d'œuvre, architecte, bureau d'étude, BET |
| Nombre de bâtiments neufs | — | RC ass., CCTP ass., CCAP ass. | bâtiment(s) neuf(s) |
| Nombre de bâtiments existants | — | RC ass., CCTP ass., CCAP ass. | bâtiment(s) existant(s) |
| Nombre de niveaux par bâtiment | — | RC ass., CCTP ass., CCAP ass. | niveau(x), R+n, sous-sol |
| Date de début des travaux | — | RC ass., CCTP ass., CCAP ass. | début des travaux, démarrage des travaux |
| Date prévisionnelle de fin | — | RC ass., CCTP ass., CCAP ass. | fin prévisionnelle, durée des travaux, délai d'exécution |
| Réception échelonnée | — | RC ass., CCTP ass., CCAP ass. | réception échelonnée, réceptions partielles |
| Missions du bureau de contrôle | Liste des missions (L, LE, PS…) | RC ass., CCTP ass., CCAP ass. | mission(s) L, mission(s) PS, missions du bureau de contrôle |
| Étude de sol | Rechercher G2 AVP, G2 PRO, G5 | TECH/ETUDE DE SOL | G2 AVP, G2 PRO, G5, étude géotechnique |

### Section complémentaire (à vérifier)

| Champ | Résultat attendu | Fichiers de référence prioritaires | Indices |
|---|---|---|---|
| Distance des avoisinants | — | RC ass., CCTP ass., CCAP ass. | avoisinant, mitoyen, distance |
| Existence d'un référé préventif / constat d'huissier | Si avoisinants | RC ass., CCTP ass., CCAP ass. | référé préventif, constat d'huissier |
| Mission AV | Si avoisinants | RC ass., CCTP ass., CCAP ass. | mission AV, avoisinants |
| Parties enterrées | — | Notice archi, CCTP, plans | sous-sol, partie enterrée, infrastructure enterrée, niveau(x) enterré(s) |
| Niveau des plus hautes eaux | Rechercher EE, EB, EH | Étude de sol | plus hautes eaux, EE, EB, EH, niveau piézométrique |
| Stratigraphie | Couches du sous-sol, lithologie | Étude de sol | stratigraphie, lithologie, couches du sous-sol |

**Livrable de l'étape 3** — un tableau :

`Champ | Section | Valeur | Source(s) | Citation | Incohérence (oui/non + détail si applicable)`

## Livrables finaux attendus

1. Un dossier `organized/` contenant la copie triée et renommée (le dossier `source/` reste
   intact, jamais modifié).
2. Un rapport unique `rapport_analyse.md` regroupant : un résumé chiffré (nb fichiers totaux,
   nb fichiers classés par catégorie, nb pièces présentes/absentes/partielles, nb champs
   trouvés/absents/incohérents), puis les 3 tableaux des étapes 1, 2 et 3.

## Règles transverses

- Ne jamais halluciner : toute affirmation (classement, présence de pièce, valeur extraite)
  doit être appuyée par une citation ou un élément concret du document.
- Ne jamais supprimer, renommer ou écraser un fichier de `source/`.
- Tout fichier non classable va dans `organized/AUTRES` — jamais perdu, jamais ignoré.
- Signale explicitement toute incertitude (« à vérifier ») plutôt que d'affirmer à tort.
