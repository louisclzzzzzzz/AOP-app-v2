"""Dédoublonnage des avis entre BOAMP et JOUE/TED.

Une consultation française au-dessus des seuils européens est publiée aux DEUX endroits : sans
dédoublonnage, chaque marché d'assurance construction significatif apparaîtrait deux fois dans
la liste de veille. Les deux publications ne partagent aucun identifiant commun exploitable —
l'`idweb` BOAMP et le numéro de publication TED sont indépendants — il faut donc les rapprocher
sur les données métier.

Empreinte retenue : acheteur + date limite de remise des offres. C'est le couple le plus stable
entre les deux sources (l'objet, lui, est souvent reformulé d'une publication à l'autre, et le
titre TED est parfois traduit). Deux consultations distinctes du même acheteur qui tomberaient
le même jour à la même heure seraient fusionnées à tort — cas assez théorique, et la fusion
reste non destructive : les deux avis d'origine restent listés et consultables sur la fiche.

En cas de fusion, TED est retenu comme avis principal : lui seul publie le lien direct vers le
DCE (BT-15), qui conditionne le retrait automatique.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.veille.criteria import normalize
from app.veille.notice import Notice, NoticeSource


@dataclass
class MergedNotice:
    """Un avis retenu, éventuellement publié sur les deux sources."""

    primary: Notice
    duplicates: list[Notice] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return [self.primary.source] + [d.source for d in self.duplicates]

    def best_dce_url(self) -> str | None:
        """Le meilleur lien de retrait disponible, toutes publications confondues.

        Le classement se fait sur l'EXPLOITABILITÉ du lien, pas sur la source qui le publie.
        Constaté sur le flux réel : TED publie fréquemment, pour une consultation PLACE, la
        racine de la plateforme (`marches-publics.gouv.fr/entreprise`) — sans identifiant de
        consultation, donc inutilisable — alors que le jumeau BOAMP porte l'URL complète avec
        son `id`. Prendre le lien de l'avis principal ferait perdre le seul lien exploitable."""
        from app.veille.retrieval import plan_retrieval

        candidates = [n.dce_url for n in (self.primary, *self.duplicates) if n.dce_url]
        for url in candidates:
            if plan_retrieval(url)[1]:
                return url
        return candidates[0] if candidates else None


def _fingerprint(notice: Notice) -> str | None:
    """Clé de rapprochement, ou None si l'avis n'a pas de quoi être rapproché de façon sûre —
    auquel cas il n'est jamais fusionné (mieux vaut un doublon visible qu'une fusion à tort)."""
    buyer = normalize(notice.buyer_name or "")
    if not buyer or notice.deadline_at is None:
        return None
    return f"{buyer}|{notice.deadline_at.date().isoformat()}"


def _source_rank(notice: Notice) -> int:
    """TED d'abord : c'est la source qui porte le lien direct vers le DCE."""
    return 0 if notice.source == NoticeSource.TED.value else 1


def merge_notices(notices: list[Notice]) -> list[MergedNotice]:
    """Fusionne les publications d'une même consultation, en conservant l'ordre d'arrivée."""
    merged: list[MergedNotice] = []
    by_fingerprint: dict[str, MergedNotice] = {}

    for notice in sorted(notices, key=_source_rank):
        fingerprint = _fingerprint(notice)
        existing = by_fingerprint.get(fingerprint) if fingerprint else None
        if existing is None:
            entry = MergedNotice(primary=notice)
            merged.append(entry)
            if fingerprint:
                by_fingerprint[fingerprint] = entry
        else:
            existing.duplicates.append(notice)

    return merged
