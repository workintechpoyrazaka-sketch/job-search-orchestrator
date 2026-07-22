import hmac
import os

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Required at boot, exactly like DATABASE_URL in db.py — the operator surface
# must not be able to start without a token configured. A missing env var
# fails closed (no unauthenticated write path by omission), it does not default
# to "open". Generate once, out of band, and set as an env var (Railway config
# var / locally exported); never commit it:
#     python -c "import secrets; print(secrets.token_urlsafe(32))"
OPERATOR_TOKEN = os.environ["OPERATOR_TOKEN"]

# auto_error=False: we own the failure path, so a MISSING header and a WRONG
# token return the same 401 — an attacker learns nothing about which one failed.
_bearer = HTTPBearer(auto_error=False)


def require_operator(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Fail-closed bearer check for the operator surface.

    Constant-time compare so a wrong token leaks no timing signal. Returns
    nothing: being authorized is the whole result. The DB role, not this
    check, decides what the request may then do.
    """
    supplied = creds.credentials if creds else ""
    if not hmac.compare_digest(supplied, OPERATOR_TOKEN):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "operator authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
