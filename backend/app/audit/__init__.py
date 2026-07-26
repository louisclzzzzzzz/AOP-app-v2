"""Audit des risques — Phase 2 du protocole d'analyse (refs/PHASE ANALYSE/00_PROTOCOLE.md).

Audit technique critique DO/TRC section par section (A→G) : pour chaque section d'ouvrage, un
appel LLM relit les documents pivots (CCTP du lot, étude de sol, RICT) et croise les données
publiques Géorisques (séisme, RGA, inondation, radon, cavités) pour produire une liste de risques
structurés (statut 🔴/🟠/🟢, exposé, analyse d'expert, impact assurabilité, recommandation). Le
rapport final assemble un tableau récapitulatif synoptique et l'analyse détaillée par section.

Distinct de la Phase 1 (`app/synthesis/`, synthèse narrative du projet) : ici l'objet n'est pas de
décrire le projet mais de l'auditer et de statuer sur l'acceptabilité de chaque risque en
souscription.
"""
