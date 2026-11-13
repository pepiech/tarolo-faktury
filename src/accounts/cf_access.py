"""Ověření JWT, které Cloudflare Access posílá v hlavičce Cf-Access-Jwt-Assertion."""

import json
import logging
import threading
import time
import urllib.request

import jwt
from django.conf import settings

logger = logging.getLogger(__name__)


class AccessTokenError(Exception):
    pass


_lock = threading.Lock()
_cache = {"keys": {}, "fetched_at": 0.0}


def _fetch_jwks():
    url = f"https://{settings.CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs"
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - pevná https adresa
        return json.load(response)


def _keys(force=False):
    if settings.CF_ACCESS_STATIC_JWKS:
        return {k["kid"]: k for k in settings.CF_ACCESS_STATIC_JWKS["keys"]}
    with _lock:
        fresh = time.monotonic() - _cache["fetched_at"] < settings.CF_ACCESS_JWKS_TTL
        if _cache["keys"] and fresh and not force:
            return _cache["keys"]
        try:
            data = _fetch_jwks()
            _cache["keys"] = {k["kid"]: k for k in data.get("keys", [])}
            _cache["fetched_at"] = time.monotonic()
        except Exception as exc:  # síť, JSON
            logger.warning("Nelze načíst JWKS Cloudflare Access: %s", exc)
            if not _cache["keys"]:
                raise AccessTokenError("JWKS nedostupné") from exc
        return _cache["keys"]


def clear_cache():
    with _lock:
        _cache["keys"] = {}
        _cache["fetched_at"] = 0.0


def verify_access_jwt(token):
    """Vrátí claims ověřeného tokenu, jinak vyhodí AccessTokenError."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AccessTokenError("neplatná hlavička tokenu") from exc
    if header.get("alg") != "RS256":
        raise AccessTokenError("nepodporovaný algoritmus")
    kid = header.get("kid")
    keys = _keys()
    if kid not in keys:
        keys = _keys(force=True)
    if kid not in keys:
        raise AccessTokenError("neznámý klíč")
    key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(keys[kid]))
    try:
        return jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=settings.CF_ACCESS_AUD,
            issuer=f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
            leeway=60,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise AccessTokenError(str(exc)) from exc
