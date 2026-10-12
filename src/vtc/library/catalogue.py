from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import httpx

from vtc.library.constants import CATALOGUE_ORIGIN, CATALOGUE_VID


PRIMO_ORIGIN = CATALOGUE_ORIGIN
PRIMO_HOST = "vtc.primo.exlibrisgroup.com"
VID = CATALOGUE_VID
INSTITUTION = "852VTC_INST"
SOURCE = "library_api"

_GUEST_JWT_PATH = f"/primaws/rest/pub/institution/{INSTITUTION}/guestJwt"
_SEARCH_PATH = "/primaws/rest/pub/pnxs"
_ACCOUNT_PATHS = {
    "overview": "/primaws/rest/priv/myaccount/counters",
    "loans": "/primaws/rest/priv/myaccount/loans",
    "requests": "/primaws/rest/priv/myaccount/requests",
    "fines": "/primaws/rest/priv/myaccount/fines",
}
_SCOPES = {
    "all": ("MyInst_and_CI", "Everything"),
    "catalogue": ("MyInstitution", "LibraryCatalog"),
    "books": ("Books", "Books"),
    "articles": ("CentralIndex", "CentralIndex"),
    "media": ("Media", "Media"),
    "videos": ("VTCeVideo", "VTCeVideo"),
}
_REQUEST_GROUPS = {
    "holds": ("hold", "holds"),
    "photocopies": ("photocopy", "photocopies"),
    "bookings": ("booking", "bookings"),
    "cdls": ("cdl", "cdls"),
    "ills": ("ill", "ill"),
    "callslips": ("callslip", "callslips"),
    "acqs": ("acq", "acq"),
    "shortloans": ("shortloan", "shortloans"),
}


class LibraryCatalogueClient:
    def __init__(
        self,
        *,
        session_file: Path | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.session_file = session_file
        self.timeout = timeout
        self.transport = transport
        self.http = httpx.Client(
            base_url=PRIMO_ORIGIN,
            headers={
                "Accept": "application/json",
                "User-Agent": "vtc-cli library catalogue reader",
                "is-nde": "true",
            },
            follow_redirects=False,
            timeout=timeout,
            transport=transport,
        )
        self._guest_jwt: str | None = None
        self._account_http: httpx.Client | None = None

    def __enter__(self) -> LibraryCatalogueClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self.http.close()
        if self._account_http is not None:
            self._account_http.close()
            self._account_http = None

    def search(
        self,
        query: str,
        scope: str = "all",
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        query = _normalize_query(query)
        scope_config = _SCOPES.get(scope)
        if not query or scope_config is None or not _valid_page(offset, limit):
            return _result(offset=offset, limit=limit, error="invalid_parameters")
        search_scope, tab = scope_config
        return self._public_search(
            query=query,
            offset=offset,
            limit=limit,
            params={"scope": search_scope, "tab": tab},
        )

    def journals(self, query: str, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        query = _normalize_query(query)
        if not query or not _valid_page(offset, limit):
            return _result(offset=offset, limit=limit, error="invalid_parameters")
        normalized = f"any,contains,{query}"
        return self._public_search(
            query=query,
            offset=offset,
            limit=limit,
            params={
                "scope": "jsearch_scope",
                "tab": "jsearch_slot",
                "q": normalized,
                "journals": normalized,
                "pcAvailability": "true",
            },
        )

    def record(self, record_id: str) -> dict[str, Any]:
        record_id = record_id.strip() if isinstance(record_id, str) else ""
        context = _record_context(record_id)
        if context is None:
            return _result(error="invalid_record_id")
        token, error = self._get_guest_jwt()
        if token is None:
            return _result(error=error)
        payload, error = self._request_json(
            self.http,
            f"{_SEARCH_PATH}/{context}/{record_id}",
            params={"scope": "MyInst_and_CI", "lang": "en", "vid": VID},
            token=token,
        )
        if payload is None:
            return _result(error=error)
        if not _valid_catalogue_doc(payload):
            return _result(error="unexpected_schema")
        returned_id = _first(payload["pnx"].get("control", {}).get("recordid"))
        if returned_id != record_id:
            return _result(error="unexpected_record")
        return _result(items=[_catalogue_item(payload)], total=1, offset=0, limit=1)

    def account(self, kind: str = "loans") -> dict[str, Any]:
        if kind not in _ACCOUNT_PATHS:
            return _result(error="invalid_account_kind")
        client, error = self._account_client()
        if client is None:
            return _result(error=error)

        counters, error = self._request_json(
            client,
            _ACCOUNT_PATHS["overview"],
            params={"lang": "en", "vid": VID},
        )
        gate = _parse_counters(counters)
        if gate is None:
            return _result(error=error or "authentication_unverified")
        actions, accounts = gate
        if kind == "overview":
            return _result(
                items=actions,
                total=len(actions),
                offset=0,
                accounts=accounts,
            )

        params = {"lang": "en", "vid": VID}
        if kind == "loans":
            params["type"] = "active"
        payload, error = self._request_json(client, _ACCOUNT_PATHS[kind], params=params)
        if payload is None:
            return _result(error=error)
        if not _business_response_ok(payload):
            return _result(error="account_response_unverified")
        items = _parse_account_items(kind, payload, actions)
        if items is None:
            return _result(error="unexpected_schema")
        return _result(items=items, total=len(items), offset=0, limit=len(items))

    def _public_search(
        self,
        *,
        query: str,
        offset: int,
        limit: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        token, error = self._get_guest_jwt()
        if token is None:
            return _result(offset=offset, limit=limit, error=error)
        request_params = {
            "q": f"any,contains,{query}",
            "scope": params["scope"],
            "tab": params["tab"],
            "offset": offset,
            "limit": limit,
            "lang": "en",
            "vid": VID,
            **{key: value for key, value in params.items() if key not in {"scope", "tab"}},
        }
        payload, error = self._request_json(
            self.http,
            _SEARCH_PATH,
            params=request_params,
            token=token,
        )
        if payload is None:
            return _result(offset=offset, limit=limit, error=error)
        if not isinstance(payload, dict):
            return _result(offset=offset, limit=limit, error="unexpected_schema")
        info = payload.get("info")
        docs = payload.get("docs")
        if not isinstance(info, dict) or not isinstance(info.get("total"), int):
            return _result(offset=offset, limit=limit, error="unexpected_schema")
        if not isinstance(docs, list) or any(not _valid_catalogue_doc(item) for item in docs):
            return _result(offset=offset, limit=limit, error="unexpected_schema")
        return _result(
            items=[_catalogue_item(item) for item in docs],
            total=info["total"],
            offset=offset,
            limit=limit,
        )

    def _get_guest_jwt(self) -> tuple[str | None, str | None]:
        if self._guest_jwt is not None and _jwt_not_expired(self._guest_jwt):
            return self._guest_jwt, None
        payload, error = self._request_json(
            self.http,
            _GUEST_JWT_PATH,
            params={"viewId": VID, "lang": "en", "isGuest": "true", "vid": VID},
        )
        if not isinstance(payload, str) or not _jwt_not_expired(payload):
            return None, error or "guest_session_unverified"
        self._guest_jwt = payload
        return payload, None

    def _account_client(self) -> tuple[httpx.Client | None, str | None]:
        if self._account_http is not None:
            return self._account_http, None
        path = self.session_file or _default_session_path()
        if not path.exists():
            return None, "missing_session"
        try:
            storage = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, "invalid_session"
        if not isinstance(storage, dict):
            return None, "invalid_session"
        token = _storage_jwt(storage)
        claims = _decode_jwt(token) if token else None
        if not _authenticated_claims(claims):
            return None, "authentication_unverified"

        client = httpx.Client(
            base_url=PRIMO_ORIGIN,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "vtc-cli library catalogue reader",
                "is-nde": "true",
            },
            follow_redirects=False,
            timeout=self.timeout,
            transport=self.transport,
        )
        for cookie in storage.get("cookies") or []:
            if not isinstance(cookie, dict) or not cookie.get("name"):
                continue
            domain = str(cookie.get("domain") or PRIMO_HOST).lstrip(".").casefold()
            if PRIMO_HOST != domain and not PRIMO_HOST.endswith(f".{domain}"):
                continue
            client.cookies.set(
                str(cookie["name"]),
                str(cookie.get("value") or ""),
                domain=str(cookie.get("domain") or PRIMO_HOST),
                path=str(cookie.get("path") or "/"),
            )
        self._account_http = client
        return client, None

    @staticmethod
    def _request_json(
        client: httpx.Client,
        path: str,
        *,
        params: dict[str, Any],
        token: str | None = None,
    ) -> tuple[Any | None, str | None]:
        allowed = {_GUEST_JWT_PATH, _SEARCH_PATH, *_ACCOUNT_PATHS.values()}
        is_record = path.startswith(f"{_SEARCH_PATH}/") and _safe_record_path(path)
        if path not in allowed and not is_record:
            return None, "endpoint_blocked"
        headers = {"Authorization": f"Bearer {token}"} if token else None
        try:
            response = client.get(path, params=params, headers=headers)
        except httpx.HTTPError:
            return None, "request_failed"
        if response.is_redirect:
            return None, "unexpected_redirect"
        if response.status_code in {401, 403}:
            return None, "authentication_unverified"
        if response.status_code < 200 or response.status_code >= 300:
            return None, "http_error"
        if response.url.host != PRIMO_HOST:
            return None, "unexpected_origin"
        if "json" not in response.headers.get("content-type", "").casefold():
            return None, "unexpected_content_type"
        try:
            return response.json(), None
        except json.JSONDecodeError:
            return None, "invalid_json"


def _default_session_path() -> Path:
    from vtc.paths import library_session_path

    return library_session_path()


def _result(
    *,
    items: list[dict[str, Any]] | None = None,
    total: int | None = None,
    offset: int = 0,
    limit: int | None = None,
    error: str | None = None,
    **data: Any,
) -> dict[str, Any]:
    body = {
        "items": items or [],
        "total": total,
        "offset": offset,
        "limit": limit,
        **data,
    }
    result: dict[str, Any] = {
        "verified": error is None,
        "source": SOURCE,
        "data": body,
    }
    if error is not None:
        result["error"] = error
    return result


def _valid_page(offset: int, limit: int) -> bool:
    return (
        isinstance(offset, int)
        and not isinstance(offset, bool)
        and offset >= 0
        and isinstance(limit, int)
        and not isinstance(limit, bool)
        and 1 <= limit <= 100
    )


def _normalize_query(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace(",", " ").split())[:500]


def _record_context(record_id: str) -> str | None:
    if not record_id or len(record_id) > 512:
        return None
    if not all(character.isalnum() or character in "._:-" for character in record_id):
        return None
    if record_id.startswith("alma"):
        return "L"
    if record_id.startswith("cdi_"):
        return "PC"
    return None


def _safe_record_path(path: str) -> bool:
    parts = path.removeprefix(f"{_SEARCH_PATH}/").split("/")
    return len(parts) == 2 and parts[0] in {"L", "PC"} and _record_context(parts[1]) == parts[0]


def _catalogue_item(payload: dict[str, Any]) -> dict[str, Any]:
    pnx = payload.get("pnx") if isinstance(payload.get("pnx"), dict) else {}
    display = pnx.get("display") if isinstance(pnx.get("display"), dict) else {}
    control = pnx.get("control") if isinstance(pnx.get("control"), dict) else {}
    delivery = payload.get("delivery") if isinstance(payload.get("delivery"), dict) else {}
    pnx_delivery = pnx.get("delivery") if isinstance(pnx.get("delivery"), dict) else {}
    categories = delivery.get("deliveryCategory") or pnx_delivery.get("fulltext") or []
    if not isinstance(categories, list):
        categories = []
    return {
        "record_id": _first(control.get("recordid")),
        "context": payload.get("context"),
        "title": _first(display.get("title")),
        "creators": _strings(display.get("creator")),
        "contributors": _strings(display.get("contributor")),
        "resource_type": _first(display.get("type")),
        "publication_date": _first(display.get("creationdate")),
        "publisher": _strings(display.get("publisher")),
        "language": _strings(display.get("language")),
        "subjects": _strings(display.get("subject")),
        "description": _strings(display.get("description")),
        "identifiers": _strings(display.get("identifier")),
        "availability": delivery.get("displayedAvailability"),
        "online": any(value in {"Alma-E", "Remote Search Resource", "fulltext"} for value in categories),
        "pnx": pnx,
        "delivery": delivery,
    }


def _valid_catalogue_doc(payload: Any) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("pnx"), dict):
        return False
    control = payload["pnx"].get("control")
    record_ids = control.get("recordid") if isinstance(control, dict) else None
    return (
        isinstance(record_ids, list)
        and len(record_ids) > 0
        and all(isinstance(record_id, str) and record_id for record_id in record_ids)
    )


def _parse_counters(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    if not _business_response_ok(payload) or not isinstance(payload.get("data"), dict):
        return None
    data = payload["data"]
    actions = data.get("listofactions", {}).get("action")
    if not isinstance(actions, list) or any(not isinstance(item, dict) for item in actions):
        return None
    required_actions = {
        str(item.get("type") or ""): item.get("value")
        for item in actions
        if item.get("type") in {"Loans", "Requests", "Fines"}
    }
    if set(required_actions) != {"Loans", "Requests", "Fines"}:
        return None
    if any(not _is_number(value) for value in required_actions.values()):
        return None

    accounts_list = data.get("accountslist")
    if accounts_list is None:
        accounts: list[dict[str, Any]] = []
    else:
        if not isinstance(accounts_list, dict) or not isinstance(accounts_list.get("account"), list):
            return None
        accounts = accounts_list["account"]
        if any(not isinstance(item, dict) or not item.get("institutioncode") for item in accounts):
            return None
    return actions, accounts


def _parse_account_items(
    kind: str,
    payload: Any,
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    if not _business_response_ok(payload) or not isinstance(payload.get("data"), dict):
        return None
    data = payload["data"]
    if kind == "loans":
        loans = data.get("loans")
        if "loans" not in data:
            return [] if _counter_is_zero(actions, "Loans") else None
        if not isinstance(loans, dict):
            return None
        items = loans.get("loan")
        if isinstance(items, list) and all(isinstance(item, dict) for item in items):
            return items if _counter_allows_items(actions, "Loans", items) else None
        return None
    if kind == "fines":
        fines = data.get("fines")
        if "fines" not in data:
            return [] if _counter_is_zero(actions, "Fines") else None
        if not isinstance(fines, dict):
            return None
        items = fines.get("fine")
        if isinstance(items, list) and all(isinstance(item, dict) for item in items):
            return items if _counter_allows_items(actions, "Fines", items) else None
        return None
    if kind == "requests":
        items: list[dict[str, Any]] = []
        found_schema = False
        for group, (item_key, request_type) in _REQUEST_GROUPS.items():
            container = data.get(group)
            if group not in data:
                continue
            if not isinstance(container, dict) or not isinstance(container.get(item_key), list):
                return None
            found_schema = True
            for item in container[item_key]:
                if not isinstance(item, dict):
                    return None
                items.append({**item, "request_type": request_type})
        if found_schema:
            return items if _counter_allows_items(actions, "Requests", items) else None
        return [] if _counter_is_zero(actions, "Requests") else None
    return None


def _counter_is_zero(actions: list[dict[str, Any]], kind: str) -> bool:
    value = next((item.get("value") for item in actions if item.get("type") == kind), None)
    try:
        return float(value) == 0
    except (TypeError, ValueError):
        return False


def _counter_allows_items(
    actions: list[dict[str, Any]],
    kind: str,
    items: list[dict[str, Any]],
) -> bool:
    value = next((item.get("value") for item in actions if item.get("type") == kind), None)
    try:
        count = float(value)
    except (TypeError, ValueError):
        return False
    return count > 0 if items else count == 0


def _business_response_ok(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and payload.get("reply-code") == "0000"
    )


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number not in {float("inf"), float("-inf")}


def _storage_jwt(storage: dict[str, Any]) -> str | None:
    entries = storage.get("sessionStorage") or []
    if isinstance(entries, list):
        for entry in entries:
            if (
                isinstance(entry, dict)
                and entry.get("origin") == PRIMO_ORIGIN
                and entry.get("name") == "primoExploreJwt"
                and isinstance(entry.get("value"), str)
            ):
                return entry["value"]
    for origin in storage.get("origins") or []:
        if not isinstance(origin, dict) or origin.get("origin") != PRIMO_ORIGIN:
            continue
        for storage_name in ("sessionStorage", "localStorage"):
            values = origin.get(storage_name) or []
            if not isinstance(values, list):
                continue
            for entry in values:
                if (
                    isinstance(entry, dict)
                    and entry.get("name") == "primoExploreJwt"
                    and isinstance(entry.get("value"), str)
                ):
                    return entry["value"]
    return None


def _decode_jwt(token: str) -> dict[str, Any] | None:
    try:
        encoded = token.split(".")[1]
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded))
    except (IndexError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _jwt_not_expired(token: str) -> bool:
    claims = _decode_jwt(token)
    expires = claims.get("exp") if claims else None
    return isinstance(expires, (int, float)) and expires > time.time()


def _authenticated_claims(claims: dict[str, Any] | None) -> bool:
    if not isinstance(claims, dict):
        return False
    signed_in = claims.get("signedIn")
    return (
        claims.get("iss") == "Prima"
        and claims.get("viewId") == VID
        and claims.get("institution") == INSTITUTION
        and (signed_in is True or signed_in == "true")
        and claims.get("userGroup") != "GUEST"
        and isinstance(claims.get("exp"), (int, float))
        and claims["exp"] > time.time()
    )


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else None


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
