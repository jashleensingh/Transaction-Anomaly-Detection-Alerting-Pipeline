"""Flask API that scores transactions and serves alerts."""

from __future__ import annotations

import hmac
from typing import Any, List, Optional

from flask import Flask, jsonify, request

from .config import ScoringConfig
from .models import DuplicateTransactionError, Severity, Transaction, ValidationError
from .service import AnomalyService

MAX_BATCH = 1000
MAX_BODY_BYTES = 1_000_000


def create_app(
    service: Optional[AnomalyService] = None,
    config: Optional[ScoringConfig] = None,
    api_key: Optional[str] = None,
) -> Flask:
    """Build the Flask app. If ``api_key`` is set, every route except /health needs it."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    app.json.sort_keys = False
    svc = service or AnomalyService(config)
    app.extensions["anomaly_service"] = svc

    def error(status: int, message: str):
        return jsonify({"error": message}), status

    @app.before_request
    def check_api_key():
        if not api_key or request.endpoint == "health":
            return None
        supplied = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(supplied.encode(), api_key.encode()):
            return error(401, "missing or invalid X-API-Key header")
        return None

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/stats")
    def stats():
        return jsonify(svc.stats())

    @app.post("/transactions")
    def post_transactions():
        payload: Any = request.get_json(silent=True)
        if payload is None:
            return error(400, "body must be valid JSON")
        if isinstance(payload, dict) and "transactions" in payload:
            payload = payload["transactions"]
        items: List[Any] = payload if isinstance(payload, list) else [payload]
        if not items:
            return error(400, "no transactions supplied")
        if len(items) > MAX_BATCH:
            return error(413, f"at most {MAX_BATCH} transactions per request")

        parsed: List[Transaction] = []
        problems = []
        for index, item in enumerate(items):
            try:
                parsed.append(Transaction.from_dict(item))
            except ValidationError as exc:
                problems.append({"index": index, "error": str(exc)})
        if problems:
            return jsonify({"error": "invalid transaction(s); nothing was scored", "details": problems}), 400

        results = svc.process(parsed)
        return jsonify({"results": [r.to_dict() for r in results]}), 200

    @app.get("/alerts")
    def list_alerts():
        try:
            limit = int(request.args.get("limit", 100))
            offset = int(request.args.get("offset", 0))
        except ValueError:
            return error(400, "limit and offset must be integers")
        if not 1 <= limit <= 500 or offset < 0:
            return error(400, "limit must be 1-500 and offset must be 0 or more")
        min_severity = None
        if request.args.get("min_severity"):
            min_severity = Severity.parse(request.args["min_severity"])
            if min_severity is Severity.NONE:
                return error(400, "min_severity must be LOW, MEDIUM, HIGH or CRITICAL")
        page, total = svc.query_alerts(
            min_severity=min_severity,
            account_id=request.args.get("account_id") or None,
            limit=limit,
            offset=offset,
        )
        return jsonify({"total": total, "limit": limit, "offset": offset, "alerts": [a.to_dict() for a in page]})

    @app.get("/alerts/<alert_id>")
    def get_alert(alert_id: str):
        alert = svc.get_alert(alert_id)
        if alert is None:
            return error(404, f"alert {alert_id} not found")
        return jsonify(alert.to_dict())

    @app.errorhandler(DuplicateTransactionError)
    def duplicate(exc):
        return error(409, str(exc))

    @app.errorhandler(ValidationError)
    def invalid(exc):
        return error(400, str(exc))

    @app.errorhandler(404)
    def not_found(_exc):
        return error(404, "not found")

    @app.errorhandler(405)
    def bad_method(_exc):
        return error(405, "method not allowed")

    @app.errorhandler(413)
    def too_large(_exc):
        return error(413, f"request body larger than {MAX_BODY_BYTES} bytes")

    @app.errorhandler(Exception)
    def unexpected(exc):
        app.logger.exception("unhandled error: %s", exc)
        return error(500, "internal server error")

    return app
