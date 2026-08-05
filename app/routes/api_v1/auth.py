"""API authentication: browser session or bearer token."""

import hashlib
import secrets
from datetime import datetime
from functools import wraps

from flask import jsonify, request
from flask_login import current_user

from ...backend.models import ApiToken, db
from . import api_v1_bp


def _token_from_request():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer "):].strip()
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return ApiToken.query.filter_by(token_hash=token_hash).first()


SCOPES = ("read", "operate", "full")
_RANK = {scope: rank for rank, scope in enumerate(SCOPES)}


def api_auth(required="read"):
    """Require a session or an API token with at least `required` scope.

    Scopes let a monitoring agent watch and requeue work without also
    being able to reconfigure the Hub. A browser session is always full
    access, since it belongs to a logged-in person.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if current_user.is_authenticated:
                return f(*args, **kwargs)

            api_token = _token_from_request()
            if api_token is None:
                return jsonify({"error": "Authentication required"}), 401

            granted = api_token.scope or "full"
            if _RANK.get(granted, -1) < _RANK[required]:
                return jsonify({
                    "error": f"This token has '{granted}' access but the "
                             f"request needs '{required}'."}), 403

            api_token.last_used_at = datetime.now()
            db.session.commit()
            return f(*args, **kwargs)
        return wrapper
    return decorator


def session_auth(f):
    """Session-only endpoints (token management)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Authentication required (session only)"}), 401
        return f(*args, **kwargs)
    return wrapper


def create_token(user_id, name, scope="full"):
    """Create a token for a user; returns (row, plaintext). The plaintext
    is shown exactly once and only its hash is stored."""
    if scope not in SCOPES:
        raise ValueError(f"Unknown scope '{scope}' (expected one of "
                         f"{', '.join(SCOPES)})")
    plaintext = secrets.token_urlsafe(32)
    row = ApiToken()
    row.name = name
    row.user_id = user_id
    row.scope = scope
    row.token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    db.session.add(row)
    db.session.commit()
    return row, plaintext


@api_v1_bp.route("/tokens", methods=["GET"])
@session_auth
def list_tokens():
    tokens = ApiToken.query.filter_by(user_id=current_user.id).all()
    return jsonify({"tokens": [
        {
            "id": t.id,
            "name": t.name,
            "scope": t.scope or "full",
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        } for t in tokens
    ]})


@api_v1_bp.route("/tokens", methods=["POST"])
@session_auth
def mint_token():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    if not name:
        return jsonify({"error": "'name' is required"}), 400
    try:
        row, plaintext = create_token(current_user.id, name,
                                      data.get("scope", "full"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"id": row.id, "name": row.name, "scope": row.scope,
                    "token": plaintext,
                    "note": "Store this token now; it cannot be retrieved again."}), 201


@api_v1_bp.route("/tokens/<int:token_id>", methods=["DELETE"])
@session_auth
def delete_token(token_id):
    row = ApiToken.query.filter_by(id=token_id, user_id=current_user.id).first()
    if row is None:
        return jsonify({"error": "Token not found"}), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify({"status": "deleted"})
