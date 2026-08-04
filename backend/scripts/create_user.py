"""Crée un compte (email + mot de passe) — pas d'auto-inscription, à exécuter par un
administrateur uniquement (garde l'app réservée à l'équipe, cf. app/api/auth.py).

Local :   cd backend && uv run python scripts/create_user.py quelqu.un@exemple.fr
Railway : railway ssh --service aop-v2 -- uv run python scripts/create_user.py quelqu.un@exemple.fr
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.repository import create_user, get_user_by_email  # noqa: E402
from app.store.db import init_db, session_scope  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage : uv run python scripts/create_user.py email@example.com", file=sys.stderr)
        raise SystemExit(1)
    email = sys.argv[1].strip().lower()

    init_db()
    with session_scope() as s:
        if get_user_by_email(s, email) is not None:
            print(f"Un compte existe déjà pour {email}.", file=sys.stderr)
            raise SystemExit(1)

    password = getpass.getpass("Mot de passe : ")
    if len(password) < 8:
        print("Mot de passe trop court (8 caractères minimum).", file=sys.stderr)
        raise SystemExit(1)
    if getpass.getpass("Confirmer : ") != password:
        print("Les mots de passe ne correspondent pas.", file=sys.stderr)
        raise SystemExit(1)

    with session_scope() as s:
        create_user(s, email, password)
    print(f"Compte créé pour {email}.")


if __name__ == "__main__":
    main()
