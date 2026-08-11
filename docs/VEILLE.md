# Veille automatique BOAMP / JOUE

Module `backend/app/veille/`. En amont du pipeline d'analyse : trouver les consultations
d'assurance construction publiées, et rapatrier leur DCE quand c'est possible.

Rien n'est activé par défaut hormis la recherche à la demande.

## Ce que la veille cible

Les **marchés publics de services d'assurance construction** — dommages-ouvrage, tous risques
chantier, CCRD/RCMO — c'est-à-dire les consultations auxquelles SMABTP répond, et dont le DCE
contient le projet de construction à analyser. Pas les marchés de travaux eux-mêmes (CPV 45*),
dont la taxonomie du pipeline ne relève pas.

Critères dans `backend/config/veille_criteres.yaml`, en deux temps :

1. **côté serveur** — pour ne pas rapatrier des milliers d'avis : codes CPV « services
   d'assurance » et recherche plein texte pour TED, recherche sur l'objet pour le BOAMP ;
2. **côté client** (`criteria.py`) — `require_any` puis `exclude_any` sur l'objet **et** la
   description consolidés. C'est le filtre décisif : les CPV d'assurance seuls ramènent aussi
   les flottes automobiles, la RC générale et le cyber.

Le tri est volontairement à base de mots-clés, pas d'un appel LLM : trier un flux quotidien
d'avis est un travail de tamis, pas de jugement. Il doit rester gratuit, instantané et
explicable — l'UI affiche quels termes ont retenu chaque avis, ce qu'un LLM ne garantirait pas.

Deux réglages à connaître pour affiner :

- un avis d'assurances multi-lots ne mentionne souvent « dommages-ouvrage » que dans
  l'intitulé d'un lot, jamais dans l'objet global — d'où le filtrage sur objet + description ;
- les abréviations courtes (`trc`, `puc`, `cnr`) sont recherchées sur frontières de mots, sinon
  `trc` matcherait « élec**tr**i**c**ité ».

## Les deux sources

| | BOAMP | JOUE / TED |
|---|---|---|
| API | `boamp-datadila.opendatasoft.com` (DILA, OpenDataSoft v2.1) | `api.ted.europa.eu/v3/notices/search` |
| Clé | aucune | aucune |
| Filtrage serveur | plein texte sur l'objet | CPV + pays + plein texte (requête « expert ») |
| Lien vers le DCE | **non** — seulement l'URL générique du profil d'acheteur | **oui** — BT-15 `document-url-lot`, lien direct |

C'est cette dernière ligne qui justifie d'interroger les deux : TED est nettement meilleur pour
le retrait automatique, le BOAMP rattrape les consultations sous les seuils européens (MAPA),
absentes de TED.

**Dédoublonnage** (`dedup.py`) : une consultation française au-dessus des seuils est publiée aux
deux endroits, sans identifiant commun. Le rapprochement se fait sur **acheteur + date limite**,
le couple le plus stable entre les deux sources (l'objet, lui, est souvent reformulé, et le
titre TED parfois traduit). En cas de fusion, TED devient l'avis principal ; l'avis BOAMP reste
listé sur la fiche, pour que la fusion soit vérifiable et non subie.

## Le retrait du DCE

Le DCE n'est publié ni par le BOAMP ni par TED : il vit sur la plateforme de dématérialisation
choisie par l'acheteur, et le paysage français en compte des dizaines (relevé sur un
échantillon réel de 54 avis DO/TRC : ~20 hôtes distincts).

`retrieval/` répond par un statut, jamais par un silence :

| Statut | Sens |
|---|---|
| `downloaded` | le DCE est sur le disque, le dossier est créé |
| `manual_required` | **on ne sait pas faire, et on le dit d'avance** — captcha, plateforme non prise en charge, identité non configurée |
| `retrieval_failed` | on savait faire et ça a cassé (réseau, formulaire modifié) |

La distinction entre les deux derniers compte : seul `retrieval_failed` justifie d'aller
regarder le code.

### Plateformes prises en charge

- **Famille Atexo MPE** (`atexo.py`) — PLACE (`marches-publics.gouv.fr`), Maximilien, Mégalis,
  et les portails de nombreux départements et métropoles. Détection sur la **signature d'URL**
  (`index.php?page=Entreprise.…`), pas sur une liste de domaines qu'il serait impossible de
  tenir à jour. Les noms de champs PRADO sont retrouvés par suffixe, jamais codés en dur : le
  préfixe (`ctl0$CONTENU_PAGE$…`) varie d'une version d'Atexo à l'autre.
- **Lien direct** (`direct.py`) — l'avis pointe déjà sur une archive. Reconnue à sa **signature
  binaire**, pas à son content-type : les plateformes annoncent volontiers
  `application/octet-stream`, voire `text/html`, sur un zip parfaitement valide.

### Plateformes explicitement non automatisées

`marches-publics.info`, `marches-securises.fr`, `aws-achat.info` (famille AWS) et
`achatpublic.com` protègent le retrait par un **captcha** — achatpublic pilote en plus tout le
parcours en JavaScript. Elles sont reconnues pour annoncer d'emblée un retrait manuel plutôt
qu'échouer après coup. Contourner ces captchas n'est pas envisagé : c'est une protection
délibérée de l'éditeur, et la voie prévue pour un accès automatisé y passe par un compte
fournisseur.

### Couverture réelle

Mesurée sur un balayage réel du 11/08/2026 (61 avis vus, 40 retenus) :

| | avis | |
|---|---|---|
| AWS (`marches-publics.info` + `marches-securises`) | 14 | captcha |
| achatpublic.com | 5 | captcha + JS |
| **Atexo / PLACE — automatisable** | **2** | |
| aucun lien de DCE publié | 8 | |
| divers portails non pris en charge | 11 | |

**Le retrait automatique ne couvre donc qu'une petite part du flux** (~5 % sur cet échantillon),
et non la majorité. La raison est structurelle, pas corrigeable par plus de code : les deux
plateformes dominantes sur ce segment sont protégées par captcha. Sur les avis PLACE, TED
publie de surcroît souvent la racine de la plateforme plutôt que l'URL de la consultation.

L'essentiel de la valeur du module est donc la **veille elle-même** — trouver, filtrer et
prioriser les consultations pertinentes — le retrait automatique étant un bonus là où il est
possible. Chaque avis reste immédiatement exploitable à la main : lien vers l'avis, lien vers
la plateforme, et dépôt du zip par le canal habituel.

**Retrait au bouton par défaut, jamais silencieux.** Même sur une plateforme automatisable, le
retrait ne se déclenche pas tout seul à l'issue d'une recherche : télécharger un fichier
depuis un tiers et créer un dossier n'est pas un effet anodin d'un balayage, c'est une décision
par avis. C'est le bouton « Récupérer le DCE » sur chaque avis qui déclenche `fetch_dce()`.
`AOP_VEILLE_AUTO_RETRIEVAL=true` change ce comportement et enchaîne le retrait dès qu'un
nouvel avis automatisable est trouvé — désactivé par défaut, même logique que le balayage
quotidien ci-dessous.

### Identité de retrait

Les plateformes exigent nom, prénom et e-mail — y compris en mode « téléchargement anonyme »
sur PLACE (vérifié sur le formulaire réel : les validateurs serveur refusent la soumission
sans eux). Ces coordonnées viennent de `.env` (`AOP_VEILLE_CONTACT_*`). **Sans elles, le retrait
automatique est désactivé, jamais contourné par une identité fabriquée.**

Le retrait identifié est d'ailleurs le bon défaut et pas seulement le seul possible : il inscrit
le retrait au registre de la consultation, ce qui **oblige l'acheteur à notifier toute
modification du DCE** — information critique quand on chiffre un risque sur ces pièces.

## Ce qui se passe après un retrait

Le zip est déposé dans `workspace/<dossier_id>/upload.zip` — exactement la disposition attendue
par le pipeline d'ingestion : un dossier issu de la veille est ensuite indiscernable d'un
dossier déposé à la main.

Le dossier reste au statut **`uploaded`** : aucune ingestion, aucun OCR, aucun appel LLM.
L'analyse ne démarre que sur `POST /api/dossiers/{id}/lancer` (bouton « Lancer l'analyse »).
C'est délibéré — une recherche nocturne ne doit jamais pouvoir engager de dépense d'API sans
arbitrage humain (ordre de grandeur mesuré : 0,70 à 1,00 $ par dossier complet).

## Déclenchement

- **À la demande** — bouton « Rechercher maintenant », toujours disponible.
- **Quotidien** — `AOP_VEILLE_DAILY_SCAN=true` + `AOP_VEILLE_SCAN_HOUR`. Désactivé par défaut :
  rien ne doit sortir seul vers des API externes sans décision explicite (poste de test,
  exécutable Windows distribué, suite de tests).

Le planificateur (`scheduler.py`) est une simple tâche asyncio qui dort jusqu'à la prochaine
occurrence de l'heure configurée — pas d'APScheduler ni de broker : l'application est
mono-instance et locale, un travail quotidien unique ne justifie pas un service de plus sur le
poste d'un souscripteur.

## Idempotence

`(source, source_id)` est unique. Repasser sur un avis déjà connu **rafraîchit ses données
éditoriales** (dont la date limite, qui bouge réellement en cas d'avis rectificatif) mais ne
touche **jamais** son statut : un avis écarté par l'utilisateur, ou dont le DCE a déjà été
rapatrié, ne revient pas à l'état neuf parce que la source l'a republié.

## API

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/veille/avis` | avis repérés, les plus urgents d'abord |
| `GET` | `/api/veille/etat` | dernier passage, réglages, identité configurée ou non |
| `POST` | `/api/veille/scan` | recherche à la demande |
| `POST` | `/api/veille/avis/{id}/retrait` | (re)tente le retrait du DCE |
| `POST` | `/api/veille/avis/{id}/ecarter` | écarte un avis hors périmètre |
| `POST` | `/api/dossiers/{id}/lancer` | démarre l'analyse d'un dossier rapatrié |

## Limites connues

- **Le retrait Atexo n'a pas été exercé de bout en bout sur une consultation réelle.** Tout le
  parcours a été vérifié en direct contre PLACE (réécriture d'URL, récupération du formulaire,
  découverte de chaque champ) sauf la soumission finale, qui enregistre un vrai retrait de DCE
  chez un acheteur public sous une identité réelle. À valider une fois `AOP_VEILLE_CONTACT_*`
  renseigné, sur une consultation réellement visée.
- La couverture automatique est faible (~5 % du flux, cf. « Couverture réelle » ci-dessus) et
  le restera tant que les plateformes dominantes du segment seront protégées par captcha.
- Le dédoublonnage fusionnerait à tort deux consultations distinctes d'un même acheteur ayant
  la même date limite. Cas théorique, et la fusion reste non destructive.
