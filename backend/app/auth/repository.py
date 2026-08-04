"""Accès DB pour les comptes utilisateurs (table `users`, app/store/models.py)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.store.models import User


def get_user_by_email(session: Session, email: str) -> User | None:
    stmt = select(User).where(User.email == email.strip().lower())
    return session.scalars(stmt).first()


def get_user_by_id(session: Session, user_id: str) -> User | None:
    return session.get(User, user_id)


def create_user(session: Session, email: str, password: str) -> User:
    user = User(email=email.strip().lower(), password_hash=hash_password(password))
    session.add(user)
    session.flush()
    return user
