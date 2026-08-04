"""Auth par compte (email + mot de passe), cookie de session signé.

`app.main`'s app est un singleton construit à l'import (les routers sont protégés ou non
selon AOP_REQUIRE_AUTH lu UNE SEULE FOIS à ce moment-là) — impossible de faire varier ce
réglage par test en monkeypatchant l'env après coup. On teste donc :
- la mécanique (hachage, signature de cookie, dépendances require_auth/require_auth_ws) sur
  une mini-app FastAPI jetable construite dans le test, où le comportement protégé/non
  protégé est vérifiable directement ;
- les endpoints /api/auth/* eux-mêmes (toujours montés, peu importe AOP_REQUIRE_AUTH) contre
  la vraie app ;
- que l'app réelle reste ouverte par défaut (AOP_REQUIRE_AUTH non positionné dans
  isolated_workspace) — garde-fou de non-régression pour l'usage local / l'exécutable Windows."""
from __future__ import annotations

from fastapi import Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth.dependencies import AuthenticatedUser, require_auth, require_auth_ws
from app.auth.repository import create_user
from app.auth.security import (
    COOKIE_NAME,
    create_session_cookie,
    hash_password,
    verify_password,
    verify_session_cookie,
)
from app.settings import get_settings
from app.store.db import session_scope

# `Secure` cookies (app/api/auth.py) ne sont stockés/renvoyés par httpx (TestClient) que sur
# une origine https — le base_url http://testserver par défaut les fait silencieusement
# disparaître entre deux requêtes. Sans effet sur le comportement réel : Railway ne sert que
# du https, exactement ce que ce base_url simule.
_HTTPS = "https://testserver"


# --- Hachage de mot de passe -------------------------------------------------------------

def test_password_hash_roundtrip():
    hashed = hash_password("un-mot-de-passe-correct")
    assert verify_password("un-mot-de-passe-correct", hashed) is True


def test_wrong_password_is_rejected():
    hashed = hash_password("un-mot-de-passe-correct")
    assert verify_password("autre-chose", hashed) is False


def test_malformed_stored_hash_never_raises():
    assert verify_password("peu importe", "pas-un-hash-bcrypt") is False


# --- Cookie de session signé --------------------------------------------------------------

def test_session_cookie_roundtrip():
    token = create_session_cookie("user-123", secret_key="secret-a")
    assert verify_session_cookie(token, secret_key="secret-a") == "user-123"


def test_session_cookie_rejected_with_wrong_secret():
    token = create_session_cookie("user-123", secret_key="secret-a")
    assert verify_session_cookie(token, secret_key="secret-b") is None


def test_tampered_session_cookie_is_rejected():
    token = create_session_cookie("user-123", secret_key="secret-a")
    assert verify_session_cookie(token + "x", secret_key="secret-a") is None


# --- Dépendance require_auth (HTTP), sur une mini-app jetable -----------------------------

def _protected_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_auth)])
    def protected() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_protected_route_rejects_missing_cookie(isolated_workspace):
    client = TestClient(_protected_app())
    res = client.get("/protected")
    assert res.status_code == 401


def test_protected_route_accepts_valid_session(isolated_workspace):
    with session_scope() as s:
        user = create_user(s, "louis@exemple.fr", "mot-de-passe-suffisant")
        user_id = user.id

    token = create_session_cookie(user_id, secret_key=get_settings().secret_key)
    client = TestClient(_protected_app())
    client.cookies.set(COOKIE_NAME, token)
    res = client.get("/protected")
    assert res.status_code == 200


def test_protected_route_rejects_session_of_deleted_user(isolated_workspace):
    token = create_session_cookie("un-id-qui-n-existe-pas", secret_key=get_settings().secret_key)
    client = TestClient(_protected_app())
    client.cookies.set(COOKIE_NAME, token)
    res = client.get("/protected")
    assert res.status_code == 401


# --- Dépendance require_auth_ws (WebSocket), même mini-app pattern -----------------------

def test_websocket_rejects_missing_cookie(isolated_workspace):
    app = FastAPI()

    @app.websocket("/ws-protected")
    async def protected_ws(websocket: WebSocket, _user: AuthenticatedUser = Depends(require_auth_ws)) -> None:
        await websocket.accept()

    client = TestClient(app, base_url=_HTTPS)
    try:
        with client.websocket_connect("/ws-protected"):
            assert False, "la connexion aurait dû être rejetée"
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


def test_websocket_accepts_valid_session(isolated_workspace):
    with session_scope() as s:
        user = create_user(s, "louis@exemple.fr", "mot-de-passe-suffisant")
        user_id = user.id
    token = create_session_cookie(user_id, secret_key=get_settings().secret_key)

    app = FastAPI()

    @app.websocket("/ws-protected")
    async def protected_ws(websocket: WebSocket, _user: AuthenticatedUser = Depends(require_auth_ws)) -> None:
        await websocket.accept()
        await websocket.send_text("ok")

    client = TestClient(app, base_url=_HTTPS)
    client.cookies.set(COOKIE_NAME, token)
    with client.websocket_connect("/ws-protected") as ws:
        assert ws.receive_text() == "ok"


# --- Endpoints /api/auth/* contre la vraie app (toujours montés) --------------------------

def test_login_sets_cookie_and_me_reflects_it(isolated_workspace):
    from app.main import app

    with session_scope() as s:
        create_user(s, "louis@exemple.fr", "mot-de-passe-suffisant")

    client = TestClient(app, base_url=_HTTPS)
    res = client.post("/api/auth/login", json={"email": "louis@exemple.fr", "password": "mot-de-passe-suffisant"})
    assert res.status_code == 200
    assert res.json() == {"email": "louis@exemple.fr"}
    assert COOKIE_NAME in res.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"email": "louis@exemple.fr"}


def test_login_rejects_wrong_password(isolated_workspace):
    from app.main import app

    with session_scope() as s:
        create_user(s, "louis@exemple.fr", "mot-de-passe-suffisant")

    client = TestClient(app, base_url=_HTTPS)
    res = client.post("/api/auth/login", json={"email": "louis@exemple.fr", "password": "faux"})
    assert res.status_code == 401


def test_login_rejects_unknown_email(isolated_workspace):
    from app.main import app

    client = TestClient(app, base_url=_HTTPS)
    res = client.post("/api/auth/login", json={"email": "inconnu@exemple.fr", "password": "peu importe"})
    assert res.status_code == 401


def test_me_without_session_is_401(isolated_workspace):
    from app.main import app

    client = TestClient(app, base_url=_HTTPS)
    assert client.get("/api/auth/me").status_code == 401


def test_logout_clears_cookie(isolated_workspace):
    from app.main import app

    with session_scope() as s:
        create_user(s, "louis@exemple.fr", "mot-de-passe-suffisant")

    client = TestClient(app, base_url=_HTTPS)
    client.post("/api/auth/login", json={"email": "louis@exemple.fr", "password": "mot-de-passe-suffisant"})
    assert client.get("/api/auth/me").status_code == 200

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


# --- Non-régression : l'app réelle reste ouverte par défaut (usage local / exécutable Windows) --

def test_dossiers_api_stays_open_without_auth_by_default(isolated_workspace):
    """AOP_REQUIRE_AUTH n'est pas positionné par isolated_workspace : reflète le comportement
    par défaut (local, exécutable Windows) — jamais de mur de login imposé sans configuration
    explicite."""
    from app.main import app

    client = TestClient(app, base_url=_HTTPS)
    res = client.get("/api/dossiers")
    assert res.status_code == 200
