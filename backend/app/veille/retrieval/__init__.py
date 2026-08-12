"""Retrait du dossier de consultation (DCE) sur le profil d'acheteur.

Le BOAMP et TED publient des AVIS, jamais le DCE lui-même : celui-ci vit sur la plateforme de
dématérialisation choisie par l'acheteur, et le paysage français en compte des dizaines. D'où
un adaptateur par famille de plateforme, plus un repli honnête (« retrait manuel ») quand
aucune automatisation n'est possible.
"""
from app.veille.retrieval.base import (
    RetrievalOutcome,
    RetrievalStatus,
    fetch_dce,
    plan_retrieval,
)

__all__ = ["RetrievalOutcome", "RetrievalStatus", "fetch_dce", "plan_retrieval"]
