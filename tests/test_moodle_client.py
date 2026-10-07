import httpx
import pytest

from vtc.errors import VtcError
from vtc.moodle.client import MoodleClient
from vtc.sites import parse_site


def _client_with_transport(transport):
    client = object.__new__(MoodleClient)
    client.site = parse_site("ay2627")
    client._wstoken = "private-token"
    client.http = httpx.Client(transport=transport, follow_redirects=True)
    return client


def test_file_request_adds_token_to_same_site_webservice_url_only_at_request_time():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=b"file")

    client = _client_with_transport(httpx.MockTransport(respond))
    public_url = (
        "https://moodle2627.vtc.edu.hk/webservice/pluginfile.php/1/notes.pdf"
        "?forcedownload=1"
    )

    client.get_file(public_url)

    assert public_url == (
        "https://moodle2627.vtc.edu.hk/webservice/pluginfile.php/1/notes.pdf"
        "?forcedownload=1"
    )
    assert requests[0].url.params["forcedownload"] == "1"
    assert requests[0].url.params["token"] == "private-token"


def test_file_request_does_not_forward_token_to_foreign_origin():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=b"file")

    client = _client_with_transport(httpx.MockTransport(respond))

    client.get_file("https://files.example.test/notes.pdf?token=private-token&download=1")

    assert requests[0].url.params["download"] == "1"
    assert "token" not in requests[0].url.params


def test_file_request_does_not_forward_token_on_foreign_redirect():
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.host == "moodle2627.vtc.edu.hk":
            return httpx.Response(
                302,
                headers={"location": "https://files.example.test/notes.pdf?download=1"},
            )
        return httpx.Response(200, content=b"file")

    client = _client_with_transport(httpx.MockTransport(respond))

    client.get_file(
        "https://moodle2627.vtc.edu.hk/webservice/pluginfile.php/1/notes.pdf"
    )

    assert requests[0].url.params["token"] == "private-token"
    assert requests[1].url.params["download"] == "1"
    assert "token" not in requests[1].url.params
    assert "referer" not in requests[1].headers
    assert "authorization" not in requests[1].headers


def test_moodle_page_http_failure_stays_unverified():
    client = _client_with_transport(
        httpx.MockTransport(lambda request: httpx.Response(503, text="maintenance"))
    )

    with pytest.raises(VtcError) as caught:
        client._get("/my/courses.php")

    assert caught.value.status == "unverified"


def test_moodle_page_foreign_redirect_stays_unverified():
    def respond(request):
        if request.url.host == "moodle2627.vtc.edu.hk":
            return httpx.Response(302, headers={"location": "https://unexpected.example.test/"})
        return httpx.Response(200, text="unrelated page")

    client = _client_with_transport(httpx.MockTransport(respond))

    with pytest.raises(VtcError) as caught:
        client._get("/my/courses.php")

    assert caught.value.status == "unverified"


def test_moodle_page_without_authenticated_marker_stays_unverified():
    client = _client_with_transport(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text="<html><h1>Scheduled maintenance</h1></html>",
                headers={"content-type": "text/html"},
            )
        )
    )

    with pytest.raises(VtcError) as caught:
        client._get("/my/courses.php")

    assert caught.value.status == "unverified"


def test_authenticated_assignment_page_without_logout_link_is_accepted():
    html = """
    <html>
      <body id="page-mod-assign-view" class="loggedin roleshortname-student">
        <script>M.cfg = {userid: 123, sesskey: "synthetic"};</script>
        <table class="submissionstatustable"><tr><td>Submitted</td></tr></table>
      </body>
    </html>
    """
    client = _client_with_transport(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=html,
                headers={"content-type": "text/html; charset=utf-8"},
            )
        )
    )

    response = client._get("/mod/assign/view.php?id=1")

    assert response.status_code == 200
