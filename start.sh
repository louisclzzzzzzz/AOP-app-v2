#!/usr/bin/env bash
# Lancement en une commande d'AOP v2 (build frontend + serveur unique sur localhost:8000).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "⚠️  .env introuvable — copiez .env.example vers .env et renseignez MISTRAL_API_KEY avant de continuer."
  echo "   cp .env.example .env"
fi

echo "→ Installation et build du frontend…"
(cd frontend && npm install --silent && npm run build)

echo "→ Installation des dépendances backend (uv)…"
(cd backend && uv sync --quiet)

PORT="${AOP_BACKEND_PORT:-8000}"
echo "→ Démarrage du serveur sur http://localhost:${PORT}"
(cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port "${PORT}")
