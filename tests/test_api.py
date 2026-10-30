import pytest

from anomaly_pipeline.api import MAX_BATCH, create_app


def txn(n=1, **overrides):
    data = {
        "transaction_id": f"T{n:06d}", "account_id": "ACC1", "timestamp": "2026-09-10T12:00:00Z",
        "amount": 40.0, "merchant": "Maple Grocer", "country": "CA",
    }
    data.update(overrides)
    return data


@pytest.fixture
def client():
    return create_app().test_client()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.get_json() == {"status": "ok"}


def test_score_single_transaction_creates_alert_with_reasons(client):
    r = client.post("/transactions", json=txn(1, amount=15000))
    assert r.status_code == 200
    result = r.get_json()["results"][0]
    assert result["flagged"] and result["severity"] in {"MEDIUM", "HIGH", "CRITICAL"}
    assert result["reasons"] and {"code", "kind", "points", "description"} <= set(result["reasons"][0])
    alert = client.get(f"/alerts/{result['alert_id']}").get_json()
    assert alert["transaction"]["transaction_id"] == "T000001"
    assert alert["reasons"] == result["reasons"]


def test_clean_transaction_not_flagged(client):
    result = client.post("/transactions", json=txn(1)).get_json()["results"][0]
    assert result["flagged"] is False and result["alert_id"] is None and result["severity"] == "NONE"


def test_batch_forms(client):
    r = client.post("/transactions", json={"transactions": [txn(1), txn(2)]})
    assert r.status_code == 200 and len(r.get_json()["results"]) == 2
    r = client.post("/transactions", json=[txn(3), txn(4)])
    assert r.status_code == 200 and len(r.get_json()["results"]) == 2


def test_invalid_item_rejects_whole_batch(client):
    r = client.post("/transactions", json=[txn(1), txn(2, amount=-1), txn(3, country="Canada")])
    assert r.status_code == 400
    body = r.get_json()
    assert [d["index"] for d in body["details"]] == [1, 2]
    assert client.get("/stats").get_json()["transactions_processed"] == 0


@pytest.mark.parametrize("body", [None, "plain text"])
def test_non_json_body_rejected(client, body):
    r = client.post("/transactions", data=body, content_type="text/plain")
    assert r.status_code == 400


def test_empty_list_rejected(client):
    assert client.post("/transactions", json=[]).status_code == 400


def test_batch_size_limit(client):
    big = [txn(i) for i in range(MAX_BATCH + 1)]
    assert client.post("/transactions", json=big).status_code == 413


def test_oversized_body(client):
    app = create_app()
    app.config["MAX_CONTENT_LENGTH"] = 200
    r = app.test_client().post("/transactions", json=[txn(i) for i in range(5)])
    assert r.status_code == 413


def test_duplicate_id_conflict(client):
    assert client.post("/transactions", json=txn(1)).status_code == 200
    r = client.post("/transactions", json=txn(1))
    assert r.status_code == 409 and "already processed" in r.get_json()["error"]


def test_list_alerts_filters(client):
    client.post("/transactions", json=[
        txn(1, account_id="A", amount=15000),
        txn(2, account_id="B", amount=5500, timestamp="2026-09-10T12:01:00Z"),
    ])
    body = client.get("/alerts").get_json()
    assert body["total"] == 2 and body["alerts"][0]["score"] >= body["alerts"][1]["score"]
    assert client.get("/alerts?account_id=B").get_json()["total"] == 1
    assert client.get("/alerts?min_severity=critical").get_json()["total"] <= 2
    assert len(client.get("/alerts?limit=1").get_json()["alerts"]) == 1


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "limit=abc", "offset=-1", "min_severity=none", "min_severity=scary"])
def test_bad_query_params(client, query):
    assert client.get(f"/alerts?{query}").status_code == 400


def test_unknown_alert_and_route(client):
    assert client.get("/alerts/A-999999").status_code == 404
    assert client.get("/nope").status_code == 404
    assert client.delete("/alerts").status_code == 405


def test_stats(client):
    client.post("/transactions", json=[txn(1, amount=15000), txn(2, timestamp="2026-09-10T13:00:00Z")])
    s = client.get("/stats").get_json()
    assert s["transactions_processed"] == 2 and s["alerts_total"] == 1
    assert sum(s["alerts_by_severity"].values()) == 1


def test_api_key_enforced():
    c = create_app(api_key="s3cret").test_client()
    assert c.get("/health").status_code == 200                      # health stays open
    assert c.get("/alerts").status_code == 401
    assert c.get("/alerts", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/alerts", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert c.post("/transactions", json=txn(1)).status_code == 401
    assert c.post("/transactions", json=txn(1), headers={"X-API-Key": "s3cret"}).status_code == 200


def test_internal_errors_do_not_leak_details():
    app = create_app()
    svc = app.extensions["anomaly_service"]

    def boom(*_a, **_k):
        raise RuntimeError("secret internal detail")

    svc.process = boom
    r = app.test_client().post("/transactions", json=txn(1))
    assert r.status_code == 500 and "secret" not in r.get_data(as_text=True)
