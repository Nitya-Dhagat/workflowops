from __future__ import annotations

import httpx
import pytest

from backend.integrations.base import ConnectorAction, ConnectorError, HttpTransport, RetrieveQuery
from backend.integrations.connectors import ConnectorSet

TICKET = {
    "title": "Onboard X",
    "type": "onboarding",
    "partner_id": "partner-0011",
    "priority": "normal",
    "owner_id": "u_10",
    "description": "",
}


async def test_crm_and_kb_reads(mock_client: httpx.AsyncClient) -> None:
    r = await mock_client.get("/crm/partners", params={"q": "northwind"})
    assert [p["name"] for p in r.json()] == ["Northwind Cloud"]
    hits = (await mock_client.get("/kb/search", params={"q": "SOC 2 portal access"})).json()
    assert hits and hits[0]["id"] in {"POL-ONB-002", "POL-ONB-010", "POL-FAQ-001"}


async def test_server_side_idempotency_replays(mock_client: httpx.AsyncClient) -> None:
    h = {"Idempotency-Key": "k1"}
    a = await mock_client.post("/ticketing/tickets", json=TICKET, headers=h)
    b = await mock_client.post("/ticketing/tickets", json=TICKET, headers=h)
    assert a.status_code == 201 and b.status_code == 200 and b.json()["replayed"]
    assert a.json()["id"] == b.json()["id"]


@pytest.mark.parametrize(
    ("fault", "retryable", "ambiguous"),
    [
        ("error_500", True, True),
        ("error_400", False, False),
        ("malformed", True, True),
        ("ack_lost", True, True),
    ],
)
async def test_fault_classification(
    mock_client: httpx.AsyncClient, fault: str, retryable: bool, ambiguous: bool
) -> None:
    await mock_client.post("/_control/faults", json=[{"fault": fault, "path_prefix": "/ticketing"}])
    cs = ConnectorSet(mock_client, timeout_s=0.5)
    with pytest.raises(ConnectorError) as e:
        await cs.ticketing.execute(
            ConnectorAction(op="create_ticket", params=TICKET, idempotency_key="k2")
        )
    assert e.value.retryable is retryable and e.value.ambiguous is ambiguous


async def test_ack_lost_applies_side_effect(mock_client: httpx.AsyncClient) -> None:
    await mock_client.post(
        "/_control/faults",
        json=[{"fault": "ack_lost", "path_prefix": "/ticketing/tickets", "method": "POST"}],
    )
    r = await mock_client.post("/ticketing/tickets", json=TICKET, headers={"Idempotency-Key": "z"})
    assert r.status_code == 503
    found = (await mock_client.get("/ticketing/tickets", params={"idempotency_key": "z"})).json()
    assert len(found) == 1


async def test_timeout_is_enforced_client_side(mock_client: httpx.AsyncClient) -> None:
    await mock_client.post("/_control/faults", json=[{"fault": "timeout", "delay_s": 2}])
    t = HttpTransport(mock_client, timeout_s=0.1)
    with pytest.raises(ConnectorError) as e:
        await t.call("GET", "/meta")
    assert e.value.retryable and not e.value.ambiguous  # reads are never ambiguous


async def test_fault_count_is_consumed(mock_client: httpx.AsyncClient) -> None:
    await mock_client.post("/_control/faults", json=[{"fault": "error_500", "times": 1}])
    cs = ConnectorSet(mock_client)
    with pytest.raises(ConnectorError):
        await cs.crm.retrieve(RetrieveQuery(op="meta"))
    assert (await cs.crm.retrieve(RetrieveQuery(op="meta"))).ok


async def test_health_and_reset(mock_client: httpx.AsyncClient) -> None:
    cs = ConnectorSet(mock_client)
    assert all([(await c.health_check()).ok for c in cs.all()])
    await mock_client.post("/ticketing/tickets", json=TICKET)
    before = (await mock_client.get("/_control/state")).json()["write_count"]
    await mock_client.post("/_control/reset")
    after = (await mock_client.get("/_control/state")).json()["write_count"]
    assert before == 1 and after == 0
