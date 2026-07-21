# syntax=docker/dockerfile:1

# --- Frontend build ---
FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Backend runtime ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app/backend

# Dépendances d'abord (cache Docker) : le lock ne change pas à chaque édition de code.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY backend/ ./
RUN uv sync --frozen --no-dev

COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

ENV PATH="/app/backend/.venv/bin:${PATH}"
EXPOSE 8000

# uvicorn appelé directement (pas "uv run") pour éviter une re-synchronisation
# du venv (incluant les deps dev) à chaque démarrage de conteneur.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
