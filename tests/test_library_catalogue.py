import base64
import json
import time

import httpx
import pytest

from vtc.library.catalogue import LibraryCatalogueClient, PRIMO_ORIGIN


def _jwt(*, signed_in=False, group="GUEST", expires=3600):
    def encode(value):
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    claims = {
        "iss": "Prima",
        "viewId": "852VTC_INST:VTC",
        "institution": "852VTC_INST",
        "signedIn": signed_in,
        "userGroup": group,
        "exp": int(time.time()) + expires,
    }
    return f"{encode({'alg': 'none'})}.{encode(claims)}.signature"


def _json(request, payload, status=200):
    return httpx.Response(
        status,
        json=payload,
        headers={"content-type": "application/json"},
        request=request,
    )


def _doc(record_id="alma991234", context="L"):
    return {
        "context": context,
        "pnx": {
            "control": {"recordid": [record_id]},
            "display": {
                "title": ["Python basics"],
                "creator": ["Example, Ada"],
                "type": ["book"],
            },
        },
        "delivery": {"displayedAvailability": "Available"},
    }


def _counters(*, loans="1", requests="0", fines="0", accounts=True):
    data = {
        "listofactions": {
            "action": [
                {"type": "Fines", "value": fines},
                {"type": "Loans", "value": loans},
                {"type": "HistoryLoans", "value": "0"},
                {"type": "Requests", "value": requests},
                {"type": "Deposits", "value": "0"},
            ]
        }
    }
    if accounts:
        data["accountslist"] = {
            "account": [{"institutioncode": "852VTC_INST", "useridentifier": "student"}]
        }
    return {
        "status": "ok",
        "reply-code": "0000",
        "data": {
            **data,
        }
    }


def _session(tmp_path, token):
    path = tmp_path / "library.storage.json"
    path.write_text(
        json.dumps(
            {
                "cookies": [{"name": "JSESSIONID", "value": "private", "domain": ".exlibrisgroup.com", "path": "/"}],
                "origins": [],
                "sessionStorage": [
                    {
                        "origin": PRIMO_ORIGIN,
                        "name": "primoExploreJwt",
                        "value": token,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_search_uses_guest_bootstrap_and_never_reads_library_session(tmp_path):
    requests = []
    session = tmp_path / "library.storage.json"
    session.write_text("not json", encoding="utf-8")

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/guestJwt"):
            return _json(request, _jwt())
        return _json(
            request,
            {"info": {"total": 1}, "docs": [_doc()]},
        )

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(respond),
    ) as client:
        result = client.search("Python", offset=20, limit=10)

    assert result["verified"] is True
    assert result["source"] == "library_api"
    assert result["data"]["total"] == 1
    assert result["data"]["items"][0]["record_id"] == "alma991234"
    assert requests[1].url.path == "/primaws/rest/pub/pnxs"
    assert requests[1].url.params["q"] == "any,contains,Python"
    assert requests[1].url.params["scope"] == "MyInst_and_CI"
    assert requests[1].url.params["offset"] == "20"
    assert requests[1].headers["authorization"].startswith("Bearer ")
    assert "cookie" not in requests[1].headers


def test_search_only_verifies_empty_results_with_expected_schema():
    def respond(request):
        if request.url.path.endswith("/guestJwt"):
            return _json(request, _jwt())
        return _json(request, {"message": "no results"})

    with LibraryCatalogueClient(transport=httpx.MockTransport(respond)) as client:
        result = client.search("missing")

    assert result["verified"] is False
    assert result["error"] == "unexpected_schema"
    assert result["data"]["items"] == []
    assert result["data"]["total"] is None


def test_search_treats_commas_as_search_text_not_primo_query_syntax():
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/guestJwt"):
            return _json(request, _jwt())
        return _json(request, {"info": {"total": 0}, "docs": []})

    with LibraryCatalogueClient(transport=httpx.MockTransport(respond)) as client:
        result = client.search("Python, OR, creator")

    assert result["verified"] is True
    assert requests[1].url.params["q"] == "any,contains,Python OR creator"


def test_journals_sends_fixed_journal_scope_and_query_fields():
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/guestJwt"):
            return _json(request, _jwt())
        return _json(request, {"info": {"total": 0}, "docs": []})

    with LibraryCatalogueClient(transport=httpx.MockTransport(respond)) as client:
        result = client.journals("Nature")

    assert result["verified"] is True
    params = requests[1].url.params
    assert params["scope"] == "jsearch_scope"
    assert params["tab"] == "jsearch_slot"
    assert params["q"] == "any,contains,Nature"
    assert params["journals"] == "any,contains,Nature"


def test_record_rejects_arbitrary_url_without_making_a_request():
    requests = []

    with LibraryCatalogueClient(
        transport=httpx.MockTransport(lambda request: requests.append(request))
    ) as client:
        result = client.record("https://attacker.example/record")

    assert result["verified"] is False
    assert result["error"] == "invalid_record_id"
    assert requests == []


def test_record_uses_context_derived_from_allowlisted_record_id():
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/guestJwt"):
            return _json(request, _jwt())
        return _json(request, _doc("cdi_example_123", "PC"))

    with LibraryCatalogueClient(transport=httpx.MockTransport(respond)) as client:
        result = client.record("cdi_example_123")

    assert result["verified"] is True
    assert requests[1].url.path == "/primaws/rest/pub/pnxs/PC/cdi_example_123"


def test_account_rejects_guest_session_before_any_account_request(tmp_path):
    requests = []
    session = _session(tmp_path, _jwt())

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(lambda request: requests.append(request)),
    ) as client:
        result = client.account("loans")

    assert result["verified"] is False
    assert result["error"] == "authentication_unverified"
    assert requests == []


def test_account_requires_authenticated_counters_schema(tmp_path):
    session = _session(tmp_path, _jwt(signed_in=True, group="STUDENT"))

    def respond(request):
        return _json(request, {"data": {"loans": {"loan": []}}})

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(respond),
    ) as client:
        result = client.account("loans")

    assert result["verified"] is False
    assert result["error"] == "authentication_unverified"


def test_account_loans_uses_read_only_endpoint_after_counters_gate(tmp_path):
    requests = []
    session = _session(tmp_path, _jwt(signed_in=True, group="STUDENT"))

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/counters"):
            return _json(request, _counters())
        return _json(
            request,
            {
                "status": "ok",
                "reply-code": "0000",
                "data": {"loans": {"loan": [{"loanid": "1"}]}},
            },
        )

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(respond),
    ) as client:
        result = client.account("loans")

    assert result["verified"] is True
    assert result["data"]["items"] == [{"loanid": "1"}]
    assert [request.method for request in requests] == ["GET", "GET"]
    assert [request.url.path for request in requests] == [
        "/primaws/rest/priv/myaccount/counters",
        "/primaws/rest/priv/myaccount/loans",
    ]
    assert requests[1].url.params["type"] == "active"


def test_account_accepts_authenticated_jwt_with_real_string_signed_in_shape(tmp_path):
    session = _session(tmp_path, _jwt(signed_in="true", group="STUDENT"))

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(lambda request: _json(request, _counters())),
    ) as client:
        result = client.account("overview")

    assert result["verified"] is True


def test_account_accepts_real_counters_shape_without_accountslist(tmp_path):
    session = _session(tmp_path, _jwt(signed_in="true", group="STUDENT"))

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(
            lambda request: _json(request, _counters(accounts=False))
        ),
    ) as client:
        result = client.account("overview")

    assert result["verified"] is True
    assert result["data"]["accounts"] == []


@pytest.mark.parametrize(
    ("status", "reply_code"),
    [("error", "0000"), ("ok", "9999"), (None, "0000"), ("ok", None)],
)
def test_account_rejects_counters_without_successful_business_status(
    tmp_path, status, reply_code
):
    session = _session(tmp_path, _jwt(signed_in="true", group="STUDENT"))
    payload = _counters(accounts=False)
    payload["status"] = status
    payload["reply-code"] = reply_code

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(lambda request: _json(request, payload)),
    ) as client:
        result = client.account("overview")

    assert result["verified"] is False
    assert result["error"] == "authentication_unverified"


@pytest.mark.parametrize("signed_in", [False, "false", "True", "yes", 1, None])
def test_account_rejects_non_strict_signed_in_values(tmp_path, signed_in):
    requests = []
    session = _session(tmp_path, _jwt(signed_in=signed_in, group="STUDENT"))

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(lambda request: requests.append(request)),
    ) as client:
        result = client.account("overview")

    assert result["verified"] is False
    assert result["error"] == "authentication_unverified"
    assert requests == []


def test_account_requests_flattens_known_read_only_groups(tmp_path):
    session = _session(tmp_path, _jwt(signed_in=True, group="STUDENT"))

    def respond(request):
        if request.url.path.endswith("/counters"):
            return _json(request, _counters(requests="1"))
        return _json(
            request,
            {
                "status": "ok",
                "reply-code": "0000",
                "data": {"holds": {"hold": [{"requestid": "r1", "title": "Book"}]}},
            },
        )

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(respond),
    ) as client:
        result = client.account("requests")

    assert result["verified"] is True
    assert result["data"]["items"] == [
        {"requestid": "r1", "title": "Book", "request_type": "holds"}
    ]


def test_zero_account_items_need_counters_gate_and_zero_counter(tmp_path):
    session = _session(tmp_path, _jwt(signed_in=True, group="STUDENT"))

    def respond(request):
        if request.url.path.endswith("/counters"):
            return _json(request, _counters(loans="0"))
        return _json(request, {"status": "ok", "reply-code": "0000", "data": {}})

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(respond),
    ) as client:
        result = client.account("loans")

    assert result["verified"] is True
    assert result["data"]["items"] == []
    assert result["data"]["total"] == 0


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        ("loans", {"loans": None}),
        ("loans", {"loans": "corrupt-schema"}),
        ("loans", {"loans": {"loan": "corrupt-schema"}}),
        ("fines", {"fines": None}),
        ("fines", {"fines": "corrupt-schema"}),
        ("fines", {"fines": {"fine": "corrupt-schema"}}),
        ("requests", {"holds": None}),
    ],
)
def test_zero_counter_does_not_hide_present_corrupt_container(tmp_path, kind, data):
    session = _session(tmp_path, _jwt(signed_in=True, group="STUDENT"))

    def respond(request):
        if request.url.path.endswith("/counters"):
            return _json(request, _counters(loans="0", fines="0"))
        return _json(
            request,
            {"status": "ok", "reply-code": "0000", "data": data},
        )

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(respond),
    ) as client:
        result = client.account(kind)

    assert result["verified"] is False
    assert result["error"] == "unexpected_schema"


@pytest.mark.parametrize(
    ("kind", "counter_values", "data"),
    [
        ("loans", {"loans": "1"}, {"loans": {"loan": []}}),
        ("requests", {"requests": "1"}, {"holds": {"hold": []}}),
        ("fines", {"fines": "1"}, {"fines": {"fine": []}}),
    ],
)
def test_nonzero_counter_rejects_empty_account_list(
    tmp_path, kind, counter_values, data
):
    session = _session(tmp_path, _jwt(signed_in=True, group="STUDENT"))

    def respond(request):
        if request.url.path.endswith("/counters"):
            return _json(request, _counters(**counter_values))
        return _json(
            request,
            {"status": "ok", "reply-code": "0000", "data": data},
        )

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(respond),
    ) as client:
        result = client.account(kind)

    assert result["verified"] is False
    assert result["error"] == "unexpected_schema"


@pytest.mark.parametrize(
    ("kind", "counter_values", "data"),
    [
        ("loans", {"loans": "0"}, {"loans": {"loan": [{"loanid": "1"}]}}),
        (
            "requests",
            {"requests": "0"},
            {"holds": {"hold": [{"requestid": "1"}]}},
        ),
        ("fines", {"fines": "0"}, {"fines": {"fine": [{"fineid": "1"}]}}),
    ],
)
def test_zero_counter_rejects_nonempty_account_list(
    tmp_path, kind, counter_values, data
):
    session = _session(tmp_path, _jwt(signed_in=True, group="STUDENT"))

    def respond(request):
        if request.url.path.endswith("/counters"):
            return _json(request, _counters(**counter_values))
        return _json(
            request,
            {"status": "ok", "reply-code": "0000", "data": data},
        )

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(respond),
    ) as client:
        result = client.account(kind)

    assert result["verified"] is False
    assert result["error"] == "unexpected_schema"


@pytest.mark.parametrize(
    ("status", "reply_code"),
    [("error", "0000"), ("ok", "9999")],
)
def test_empty_account_endpoint_requires_successful_business_status(
    tmp_path, status, reply_code
):
    session = _session(tmp_path, _jwt(signed_in=True, group="STUDENT"))

    def respond(request):
        if request.url.path.endswith("/counters"):
            return _json(request, _counters(loans="0"))
        return _json(
            request,
            {"status": status, "reply-code": reply_code, "data": {}},
        )

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(respond),
    ) as client:
        result = client.account("loans")

    assert result["verified"] is False
    assert result["error"] == "account_response_unverified"


def test_invalid_account_kind_is_blocked_without_request(tmp_path):
    requests = []
    session = _session(tmp_path, _jwt(signed_in=True, group="STUDENT"))

    with LibraryCatalogueClient(
        session_file=session,
        transport=httpx.MockTransport(lambda request: requests.append(request)),
    ) as client:
        result = client.account("renew")

    assert result["verified"] is False
    assert result["error"] == "invalid_account_kind"
    assert requests == []
