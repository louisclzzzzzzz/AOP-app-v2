"""Renvois d'un rapport IA vers les documents qui le fondent.

Partagé par les deux rapports rédigés — la synthèse projet (Phase 1, app/synthesis/) et l'audit des
risques (Phase 2, app/audit/) — qui ont le même besoin et la même contrainte : leur texte est écrit
par un LLM à partir de relevés documentaires, et l'expert doit pouvoir remonter d'une affirmation
au fichier qui la justifie sans quitter sa lecture.

Le principe est une indirection en deux temps. Chaque document du prompt porte une étiquette courte
et stable (`[D1]`, `[D2]`…) : le modèle ne peut pas produire un identifiant de document fiable — un
UUID recopié de mémoire serait faux une fois sur deux — mais il recopie sans peine deux caractères
qu'il a sous les yeux. À l'assemblage, ces étiquettes (LOCALES à une section ou à un thème, chacun
renumérotant depuis D1) sont ré-attribuées en clés globales et remplacées par des marqueurs
`⟦cite:cN⟧`, livrés avec le registre qui les résout.

Les délimiteurs ⟦ ⟧ n'apparaissent jamais dans un texte rédigé en français : aucun risque de
confondre un marqueur avec une vraie parenthèse du rapport, et les exports n'ont qu'à les effacer
(§frontend `retirerMarqueursCitation`)."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Le prompt demande des étiquettes collées entre crochets (« [D1][D2] »), mais le modèle produit
# aussi, régulièrement, la forme parenthésée et virgulée (« (D1, D5) ») — 57 occurrences sur le
# premier audit réel, autant de citations perdues qui s'affichaient en texte brut. On accepte donc
# les deux délimiteurs et les séparateurs usuels : mieux vaut un parseur tolérant qu'un prompt
# qu'on espère parfaitement suivi.
REF_GROUP_RE = re.compile(r"[\[(]\s*(D\d+(?:\s*[,;/]\s*D\d+)*)\s*[\])]")
_SINGLE_REF_RE = re.compile(r"D\d+")
CITATION_MARKER_RE = re.compile(r"⟦cite:([a-z0-9]+)⟧")


@dataclass(frozen=True)
class CitationRef:
    """Un document proposé au modèle sous une étiquette, dans le prompt d'une section/d'un thème."""

    document_id: str
    filename: str
    # Le relevé factuel produit pour CE document et CE périmètre à l'étape « map » : c'est le
    # paragraphe affiché au survol de la pastille. Vide quand le document est passé par le repli
    # « extrait brut » (pas de relevé exploitable) — la pastille reste cliquable pour autant.
    excerpt: str


def strip_refs(text: str) -> str:
    """Retire les renvois d'un champ qui ne doit pas en porter (cellules de tableau synoptique) —
    les prompts les y interdisent, mais un modèle en glisse parfois un et il ne doit pas fuiter à
    l'écran."""
    return REF_GROUP_RE.sub("", text).replace("  ", " ").strip()


class CitationAllocator:
    """Convertit les renvois locaux en marqueurs globaux, en dédupliquant par (document, périmètre) :
    un document cité dix fois dans la même section n'occupe qu'une entrée du registre, avec le même
    paragraphe de survol."""

    def __init__(self) -> None:
        self.registry: dict[str, dict[str, str]] = {}
        self._keys: dict[tuple[str, str], str] = {}

    def resolve(self, text: str, *, scope: str, refs: dict[str, CitationRef]) -> str:
        def _cle(ref: CitationRef) -> str:
            identity = (ref.document_id, scope)
            key = self._keys.get(identity)
            if key is None:
                key = f"c{len(self.registry) + 1}"
                self._keys[identity] = key
                self.registry[key] = {
                    "document_id": ref.document_id,
                    "filename": ref.filename,
                    "excerpt": ref.excerpt,
                }
            return key

        def _replace(match: re.Match[str]) -> str:
            etiquettes = _SINGLE_REF_RE.findall(match.group(1))
            connues = [refs[e] for e in etiquettes if e in refs]
            if not connues:
                # Aucune étiquette du groupe n'existe. En forme canonique (« [D7] »), c'est une
                # étiquette inventée par le modèle : on l'efface. En forme parenthésée, on ne peut
                # pas l'affirmer — « (D1) » peut désigner une zone ou un repère de plan dans un
                # texte technique — donc on laisse le texte intact plutôt que de le mutiler.
                return "" if match.group(0).startswith("[") else match.group(0)
            return "".join(f"⟦cite:{_cle(ref)}⟧" for ref in connues)

        return REF_GROUP_RE.sub(_replace, text).strip()


# Consigne commune aux deux rapports, insérée dans leur prompt système respectif. Formulée une
# seule fois : les deux modèles doivent produire EXACTEMENT la même convention, sans quoi le même
# `CitationAllocator` ne saurait pas les résoudre.
REFS_PROMPT_RULES = """Renvois aux documents (impératif) : chaque document du contexte porte une \
étiquette entre crochets en tête de son bloc (« ### [D1] nom_du_fichier.pdf … »). À la FIN de \
chaque affirmation qui s'appuie sur un document, recopie l'étiquette du ou des documents qui la \
fondent, par exemple « … est fixé à 1,20 m. [D1][D4] ». Règles :
- une étiquette par crochet, collées entre elles : « [D1][D2] », JAMAIS « [D1, D2] » ni « [D1 D2] » ;
- n'utilise QUE des étiquettes réellement présentes dans le contexte fourni, jamais une étiquette \
inventée ni le nom du fichier à la place ;
- place-les en fin de phrase ou de puce, après le point final, et jamais dans un titre, un \
en-tête de tableau ou une cellule de tableau ;
- une phrase de raisonnement général (référentiel, définition, transition) qui ne s'appuie sur \
aucun document précis n'en porte aucune : mieux vaut pas d'étiquette qu'une étiquette fausse ;
- n'en mets pas à chaque phrase d'un même paragraphe si elles renvoient toutes au même document : \
une seule en fin de paragraphe suffit ;
- TROIS étiquettes au maximum pour une même affirmation. Beaucoup de données figurent dans le \
cartouche ou les généralités de TOUS les documents (nom de l'architecte, du bureau de contrôle, \
classement ERP, phasage) : ne les cite pas toutes. Retiens le ou les documents qui FONT FOI sur ce \
point — la notice de sécurité pour le classement ERP, l'acte d'engagement ou le CCAP pour les \
intervenants, l'étude de sol pour la géotechnique — et ignore les documents qui ne font que \
reprendre l'information."""
