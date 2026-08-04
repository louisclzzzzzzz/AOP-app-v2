"""Auth par code d'accès (4 chiffres, un par personne — pas de compte email/mot de passe),
cookie de session signé + verrouillage anti-brute-force par IP.

`app.main`'s app est un singleton construit à l'import (les routers sont protégés ou non
selon AOP_REQUIRE_AUTH lu UNE SEULE FOIS à ce moment-là) — impossible de faire varier ce
réglage par test en monkeypatchant l'env après coup. On teste donc :
- la mécanique (cookie signé, dépendances require_auth/require_auth_ws) sur une mini-app
  FastAPI jetable construite dans le test, où le comportement protégé/non protégé est
  vérifiable directement ;
- les endpoints /api/auth/* eux-mêmes (toujours montés, peu importe AOP_REQUIRE_AUTH) contre
  la vraie app, avec AOP_ACCESS_CODES positionné explicitement ;
- que l'app réelle reste ouverte par défaut (AOP_REQUIRE_AUTH non positionné dans
  isolated_workspace) — garde-fou de non-régression pour l'usage local / l'exécutable Windows."""
from __future__ import annotations

from fastapi import Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth import rate_limit
from app.auth.dependencies import require_auth, require_auth_ws
from app.auth.security import COOKIE_NAME, create_session_cookie, verify_access_code, verify_session_cookie
from app.settings import get_settings

# `Secure` cookies (app/api/auth.py) ne sont stockés/renvoyés par httpx (TestClient) que sur
# une origine https — le base_url http://testserver par défaut les fait silencieusement
# disparaître entre deux requêtes. Sans effet sur le comportement réel : Railway ne sert que
# du https, exactement ce que ce base_url simule.
_HTTPS = "https://testserver"


def _set_access_codes(monkeypatch, codes: str) -> None:
    monkeypatch.setenv("AOP_ACCESS_CODES", codes)
    get_settings.cache_clear()


# --- Code d'accès --------------------------------------------------------------------------

def test_correct_code_is_accepted():
    assert verify_access_code("1234", ["1234"]) is True


def test_wrong_code_is_rejected():
    assert verify_access_code("0000", ["1234"]) is False


def test_any_of_several_codes_is_accepted():
    """Un code par personne : celui de n'importe qui doit fonctionner, pas seulement le
    premier de la liste."""
    codes = ["1111", "2222", "3333"]
    assert verify_access_code("1111", codes) is True
    assert verify_access_code("2222", codes) is True
    assert verify_access_code("3333", codes) is True
    assert verify_access_code("4444", codes) is False


def test_empty_code_list_never_matches():
    """Pas de code configuré : jamais d'authentification possible, même une chaîne vide
    contre une liste vide — sinon un déploiement mal configuré (AOP_ACCESS_CODES oublié) se
    retrouverait accessible avec un champ resté vide."""
    assert verify_access_code("", []) is False


# --- Cookie de session signé --------------------------------------------------------------

def test_session_cookie_roundtrip():
    token = create_session_cookie(secret_key="secret-a")
    assert verify_session_cookie(token, secret_key="secret-a") is True


def test_session_cookie_rejected_with_wrong_secret():
    token = create_session_cookie(secret_key="secret-a")
    assert verify_session_cookie(token, secret_key="secret-b") is False


def test_tampered_session_cookie_is_rejected():
    token = create_session_cookie(secret_key="secret-a")
    assert verify_session_cookie(token + "x", secret_key="secret-a") is False


# --- Dépendance require_auth (HTTP), sur une mini-app jetable -----------------------------

def _protected_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_auth)])
    def protected() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_protected_route_rejects_missing_cookie():
    client = TestClient(_protected_app())
    assert client.get("/protected").status_code == 401


def test_protected_route_accepts_valid_session(isolated_workspace):
    token = create_session_cookie(secret_key=get_settings().secret_key)
    client = TestClient(_protected_app())
    client.cookies.set(COOKIE_NAME, token)
    assert client.get("/protected").status_code == 200


def test_protected_route_rejects_garbage_cookie(isolated_workspace):
    client = TestClient(_protected_app())
    client.cookies.set(COOKIE_NAME, "n-importe-quoi")
    assert client.get("/protected").status_code == 401


# --- Dépendance require_auth_ws (WebSocket), même mini-app pattern -----------------------

def test_websocket_rejects_missing_cookie():
    app = FastAPI()

    @app.websocket("/ws-protected")
    async def protected_ws(websocket: WebSocket, _auth: None = Depends(require_auth_ws)) -> None:
        await websocket.accept()

    client = TestClient(app, base_url=_HTTPS)
    try:
        with client.websocket_connect("/ws-protected"):
            assert False, "la connexion aurait dû être rejetée"
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


def test_websocket_accepts_valid_session(isolated_workspace):
    token = create_session_cookie(secret_key=get_settings().secret_key)

    app = FastAPI()

    @app.websocket("/ws-protected")
    async def protected_ws(websocket: WebSocket, _auth: None = Depends(require_auth_ws)) -> None:
        await websocket.accept()
        await websocket.send_text("ok")

    client = TestClient(app, base_url=_HTTPS)
    client.cookies.set(COOKIE_NAME, token)
    with client.websocket_connect("/ws-protected") as ws:
        assert ws.receive_text() == "ok"


# --- Endpoints /api/auth/* contre la vraie app (toujours montés) --------------------------

def test_login_sets_cookie_and_me_confirms_session(isolated_workspace, monkeypatch):
    from app.main import app

    rate_limit.reset_for_tests()
    _set_access_codes(monkeypatch, "1234")

    client = TestClient(app, base_url=_HTTPS)
    res = client.post("/api/auth/login", json={"code": "1234"})
    assert res.status_code == 204
    assert COOKIE_NAME in res.cookies

    assert client.get("/api/auth/me").status_code == 204


def test_login_rejects_wrong_code(isolated_workspace, monkeypatch):
    from app.main import app

    rate_limit.reset_for_tests()
    _set_access_codes(monkeypatch, "1234")

    client = TestClient(app, base_url=_HTTPS)
    assert client.post("/api/auth/login", json={"code": "0000"}).status_code == 401


def test_me_without_session_is_401(isolated_workspace):
    from app.main import app

    client = TestClient(app, base_url=_HTTPS)
    assert client.get("/api/auth/me").status_code == 401


def test_logout_clears_cookie(isolated_workspace, monkeypatch):
    from app.main import app

    rate_limit.reset_for_tests()
    _set_access_codes(monkeypatch, "1234")

    client = TestClient(app, base_url=_HTTPS)
    client.post("/api/auth/login", json={"code": "1234"})
    assert client.get("/api/auth/me").status_code == 204

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_each_persons_code_logs_in_independently(isolated_workspace, monkeypatch):
    from app.main import app

    rate_limit.reset_for_tests()
    _set_access_codes(monkeypatch, "1111,2222,3333")

    for code in ("1111", "2222", "3333"):
        client = TestClient(app, base_url=_HTTPS, headers={"X-Forwarded-For": f"9.9.8.{code[0]}"})
        assert client.post("/api/auth/login", json={"code": code}).status_code == 204


def test_revoking_one_code_does_not_affect_the_others(isolated_workspace, monkeypatch):
    """Retirer le code d'une personne de la liste (révocation) ne doit rien changer pour les
    autres — c'est tout l'intérêt d'un code par personne plutôt qu'un code partagé."""
    from app.main import app

    rate_limit.reset_for_tests()
    _set_access_codes(monkeypatch, "1111,3333")  # "2222" retiré (révoqué)

    revoked = TestClient(app, base_url=_HTTPS, headers={"X-Forwarded-For": "9.9.7.1"})
    assert revoked.post("/api/auth/login", json={"code": "2222"}).status_code == 401

    still_valid = TestClient(app, base_url=_HTTPS, headers={"X-Forwarded-For": "9.9.7.2"})
    assert still_valid.post("/api/auth/login", json={"code": "1111"}).status_code == 204


# --- Verrouillage anti-brute-force par IP --------------------------------------------------

def test_lockout_after_max_attempts(isolated_workspace, monkeypatch):
    from app.main import app

    rate_limit.reset_for_tests()
    _set_access_codes(monkeypatch, "1234")

    client = TestClient(app, base_url=_HTTPS, headers={"X-Forwarded-For": "9.9.9.1"})
    for _ in range(rate_limit.MAX_ATTEMPTS):
        assert client.post("/api/auth/login", json={"code": "0000"}).status_code == 401

    # La tentative suivante est bloquée même avec le BON code : le verrou porte sur l'IP,
    # pas sur la validité du dernier essai.
    locked = client.post("/api/auth/login", json={"code": "1234"})
    assert locked.status_code == 429


def test_lockout_is_per_ip(isolated_workspace, monkeypatch):
    from app.main import app

    rate_limit.reset_for_tests()
    _set_access_codes(monkeypatch, "1234")

    attacker = TestClient(app, base_url=_HTTPS, headers={"X-Forwarded-For": "9.9.9.2"})
    for _ in range(rate_limit.MAX_ATTEMPTS):
        attacker.post("/api/auth/login", json={"code": "0000"})
    assert attacker.post("/api/auth/login", json={"code": "1234"}).status_code == 429

    # ProxyHeadersMiddleware (app/main.py) réécrit request.client depuis X-Forwarded-For :
    # une IP différente n'est jamais affectée par le verrou de l'IP ci-dessus.
    someone_else = TestClient(app, base_url=_HTTPS, headers={"X-Forwarded-For": "9.9.9.3"})
    assert someone_else.post("/api/auth/login", json={"code": "1234"}).status_code == 204


def test_successful_login_resets_the_failure_counter(isolated_workspace, monkeypatch):
    from app.main import app

    rate_limit.reset_for_tests()
    _set_access_codes(monkeypatch, "1234")

    client = TestClient(app, base_url=_HTTPS, headers={"X-Forwarded-For": "9.9.9.4"})
    for _ in range(rate_limit.MAX_ATTEMPTS - 1):
        client.post("/api/auth/login", json={"code": "0000"})
    assert client.post("/api/auth/login", json={"code": "1234"}).status_code == 204

    # Le succès a remis le compteur à zéro : ré-échouer presque MAX_ATTEMPTS fois de suite ne
    # verrouille pas immédiatement (sinon les anciens échecs auraient survécu au succès).
    for _ in range(rate_limit.MAX_ATTEMPTS - 1):
        assert client.post("/api/auth/login", json={"code": "0000"}).status_code == 401


# --- Non-régression : l'app réelle reste ouverte par défaut (usage local / exécutable Windows) --

def test_dossiers_api_stays_open_without_auth_by_default(isolated_workspace):
    """AOP_REQUIRE_AUTH n'est pas positionné par isolated_workspace : reflète le comportement
    par défaut (local, exécutable Windows) — jamais de mur de login imposé sans configuration
    explicite."""
    from app.main import app

    client = TestClient(app, base_url=_HTTPS)
    res = client.get("/api/dossiers")
    assert res.status_code == 200
