import json

import pytest

from vtc.cli import main, redact_public_payload


def test_library_help_lists_readers_and_no_record_mutations(capsys):
    assert main(["library", "--help"]) == 0
    text = capsys.readouterr().out
    for name in ("papers", "syllabuses", "search", "loans", "requests", "resources", "hours"):
        assert name in text
    for name in ("renew", "cancel", "reserve", "pay"):
        assert name not in text


@pytest.mark.parametrize("command", ["status", "account", "loans", "requests", "fines"])
def test_library_account_missing_session_is_unverified(command, capsys):
    assert main(["library", command, "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "unverified"
    assert payload["ok"] is False
    assert payload["authenticated"] is False


def test_library_verified_empty_search_succeeds_without_private_session(monkeypatch, capsys):
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def search(self, query, **kwargs):
            assert query == "No matching title"
            assert kwargs == {"scope": "books", "offset": 20, "limit": 10}
            return {"verified": True, "source": "library_api", "data": {"items": [], "total": 0}}

    monkeypatch.setattr("vtc.library.catalogue.LibraryCatalogueClient", Client)
    assert main(["library", "search", "--query", "No matching title", "--scope", "books", "--offset", "20", "--limit", "10", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "library_api"
    assert payload["total"] == 0
    assert payload["items"] == []
    assert payload["site"] is None


def test_library_unread_public_page_does_not_report_success(monkeypatch, capsys):
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def info(self, topic):
            assert topic == "hours"
            return {"verified": False, "source": "library_html", "text": "Partial", "error": "unexpected_page"}

    monkeypatch.setattr("vtc.library.public.LibraryPublicClient", Client)
    assert main(["library", "hours", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "unverified"
    assert payload["status"] == "unverified"


def test_library_invalid_pagination_fails_before_client(monkeypatch, capsys):
    monkeypatch.setattr("vtc.library.catalogue.LibraryCatalogueClient", lambda: pytest.fail("Unexpected request"))
    assert main(["library", "search", "--query", "book", "--offset", "-1", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "usage_error"


def test_library_redaction_covers_patron_and_saml_fields():
    payload = {
        "accounts": [{"institutioncode": "TEST_LIBRARY", "UserIdentifier": "test-patron-private"}],
        "url": "https://example.test/login?SAMLRequest=test-request&RelayState=test-state&vid=TEST",
        "primoExploreJwt": "test-jwt-private",
        "AuthState": "test-auth-state",
    }
    result = redact_public_payload(payload)
    assert result["accounts"] == [{"institutioncode": "TEST_LIBRARY"}]
    assert result["url"] == "https://example.test/login?vid=TEST"
    assert "test-" not in json.dumps(result)


def test_library_private_file_login_forwarding(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    path = tmp_path / "credentials.md"

    def login(**kwargs):
        assert kwargs == {"service": "papers", "credentials_file": path, "headed": False}
        return SimpleNamespace(authenticated=True, service="papers", session=True, details="Session verified")

    monkeypatch.setattr("vtc.library.login.login_library", login)
    assert main(["login", "library", "--service", "papers", "--credentials-file", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["authenticated"] is True
    assert payload["command"] == "login.library"


def test_library_documents_status_without_session(capsys):
    assert main(["library", "status", "--service", "papers", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["service"] == "papers"
    assert payload["source"] == "unverified"


def test_library_document_search_forwards_filters_and_pagination(monkeypatch, capsys):
    class Client:
        def __init__(self, service):
            assert service == "papers"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def search(self, query, **kwargs):
            assert query == "TEST101"
            assert kwargs == {"page": 2, "per_page": 20, "filters": {"year": "2025", "session": "1"}}
            return {"verified": True, "source": "library_html", "items": [{"record_id": "42"}], "total": 21}

    monkeypatch.setattr("vtc.library.documents.LibraryDocumentsClient", Client)
    assert main(["library", "papers", "--query", "TEST101", "--year", "2025", "--session", "1", "--page", "2", "--per-page", "20", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["items"] == [{"record_id": "42"}]
    assert payload["service"] == "papers"
    assert payload["command"] == "library.papers"


def test_library_download_forwards_record_and_search(monkeypatch, tmp_path, capsys):
    class Client:
        def __init__(self, service):
            assert service == "syllabuses"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def download(self, record_id, output, **kwargs):
            assert record_id == "42"
            assert output == tmp_path
            assert kwargs == {"query": "TEST101"}
            return {"verified": True, "source": "library_html", "downloaded": True, "path": str(output / "document.pdf")}

    monkeypatch.setattr("vtc.library.documents.LibraryDocumentsClient", Client)
    assert main(["library", "download", "--service", "syllabuses", "--id", "42", "--query", "TEST101", "--output", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["downloaded"] is True
    assert payload["command"] == "library.download"


def test_library_syllabus_filters_reject_paper_only_fields_before_request(monkeypatch, capsys):
    monkeypatch.setattr("vtc.library.documents.LibraryDocumentsClient", lambda *_args: pytest.fail("Unexpected request"))
    assert main(["library", "filters", "--service", "syllabuses", "--session", "1", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "usage_error"


def test_library_human_document_output_shows_title_once(monkeypatch, capsys):
    class Client:
        def __init__(self, _service):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def search(self, *_args, **_kwargs):
            rows = [{"record_id": "42", "subject_title": "Example subject", "academic_year": "2025/2026"}]
            return {"verified": True, "source": "library_html", "items": rows, "documents": rows, "total": 1}

    monkeypatch.setattr("vtc.library.documents.LibraryDocumentsClient", Client)
    assert main(["library", "papers", "--query", "TEST101"]) == 0
    text = capsys.readouterr().out
    assert text.count("Example subject") == 1
    assert "42" in text
    assert "2025/2026" in text
