import httpx
import pytest

from vtc.library.public import LIBRARY_HOME, LibraryPublicClient


def html_response(request, body, *, status=200, headers=None):
    return httpx.Response(
        status,
        headers={"content-type": "text/html; charset=UTF-8", **(headers or {})},
        text=body,
        request=request,
    )


def test_links_returns_categorized_directory_without_fetching_external_links():
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return html_response(
            request,
            """
            <html><head><title>VTC Library Services</title></head><body>
              <a href="/suggest/">Contact Us</a>
              <ul id="nav">
                <li class="top">
                  <a class="top_link" href="/web/english/eres/eres.php"><span>E-Resources</span></a>
                  <ul class="sub">
                    <li><a href="/web/english/eres/">E-Databases</a></li>
                    <li><a href="https://provider.example/books">E-Books</a></li>
                  </ul>
                </li>
              </ul>
              <div id="main_buttons">
                <div><a href="/web/english/info/open.php"><h2>Opening Hours</h2></a></div>
              </div>
            </body></html>
            """,
        )

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.links()

    assert result["verified"] is True
    assert result["source"] == "library_html"
    assert requests == [LIBRARY_HOME]
    assert {
        (item["title"], item["category"], item["external"])
        for item in result["links"]
    } >= {
        ("E-Databases", "E-Resources", False),
        ("E-Books", "E-Resources", True),
        ("Opening Hours", "Home shortcuts", False),
    }


def test_external_redirect_is_rejected_before_external_request():
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return html_response(
            request,
            "",
            status=302,
            headers={"location": "https://login.example/?token=private"},
        )

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.links()

    assert result["verified"] is False
    assert result["error"] == "unexpected_redirect"
    assert requests == [LIBRARY_HOME]
    assert "private" not in str(result)


@pytest.mark.parametrize(
    ("category", "path", "body", "titles"),
    [
        (
            "all",
            "/web/english/eres/eres.php",
            """
            <div id="mainContent"><h1>Electronic Resources</h1>
              <h2>E-Databases</h2><p><a href="../../english/eres">E-Databases</a> by subject.</p>
              <h2>E-Books</h2><p><a href="ebk.php">E-Books</a> collection.</p>
            </div>
            """,
            {"E-Databases", "E-Books"},
        ),
        (
            "ejournals",
            "/web/english/eres/eres.php",
            """
            <div id="mainContent"><h1>Electronic Resources</h1>
              <h2>E-Journals</h2><p>Browse <a href="https://catalogue.example/journals">E-Journals</a>.</p>
            </div>
            """,
            {"E-Journals"},
        ),
        (
            "repositories",
            "/web/english/eres/eres.php",
            """
            <div id="mainContent"><h1>Electronic Resources</h1>
              <h2>VTC Institutional Repository (IR)</h2><p><a href="http://repository.vtc.edu.hk/">IR</a>.</p>
              <h2>VPET Repository</h2><p><a href="/web/english/eres/vpet.php">VPET</a>.</p>
            </div>
            """,
            {"VTC Institutional Repository (IR)", "VPET Repository"},
        ),
    ],
)
def test_resources_handles_official_markup_shapes(category, path, body, titles):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return html_response(request, body)

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.resources(category)

    assert result["verified"] is True
    assert requests == [path]
    assert result["kind"] == "directory"
    assert {item["title"] for item in result["resources"]} == titles


@pytest.mark.parametrize(
    ("category", "landing_path", "landing_heading", "frame_url"),
    [
        ("databases", "/web/english/eres/", "E-Databases", "https://libguides.vtc.edu.hk/az.php"),
        ("ebooks", "/web/english/eres/ebk.php", "E-Books", "https://libguides.vtc.edu.hk/az.php?t=36194"),
    ],
)
def test_database_collections_read_libguides_cards(
    category, landing_path, landing_heading, frame_url
):
    requests = []
    landing = f"""
    <div id="mainContent"><h1>{landing_heading}</h1>
      <iframe src="{frame_url}"></iframe>
    </div>
    """
    az_page = """
    <html><main><h1>A-Z Databases</h1>
      <input id="site_id_default" value="7341">
    </main></html>
    """
    listing = """
      <article class="az-item">
        <h3><a class="az-title" href="/az/sciencedirect">ScienceDirect</a></h3>
        <div class="az-description-short">Peer-reviewed journals and ebooks.</div>
        <div class="s-lg-az-result-subjects"><a>Engineering</a><a>Health</a></div>
        <div class="s-lg-az-result-types">E-Database; E-Book</div>
        <div class="s-lg-az-result-vendors">Elsevier</div>
      </article>
    """

    def handler(request):
        requests.append((request.url.host, request.url.path, request.url.query))
        if request.url.host == "library.vtc.edu.hk":
            return html_response(request, landing)
        if request.url.path == "/process/az/render-list":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"errText": "OK", "data": {"count": 1, "html": listing}},
                request=request,
            )
        return html_response(request, az_page)

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.resources(category, query="engineering")

    assert result["verified"] is True
    assert result["kind"] == "collection"
    assert result["result_count"] == 1
    assert result["resources"][0] == {
        "title": "ScienceDirect",
        "url": "https://libguides.vtc.edu.hk/az/sciencedirect",
        "description": "Peer-reviewed journals and ebooks.",
        "subjects": ["Engineering", "Health"],
        "types": ["E-Database", "E-Book"],
        "vendors": ["Elsevier"],
        "external": False,
    }
    assert requests[0][1] == landing_path
    assert requests[1][0] == "libguides.vtc.edu.hk"
    assert requests[2][1] == "/process/az/render-list"
    assert (b"type_id=36194" in requests[2][2]) is (category == "ebooks")


def test_video_collection_reads_selected_facet_with_scope_metadata():
    landing = """
    <div id="mainContent"><h1>eVideo Portal</h1>
      <iframe src="/web/english/eres2/evideo_embadded.php"></iframe>
    </div>
    """
    listing = """
    <html><body>
      <select id="cid"><option selected value="tvb">TVB Jade</option></select>
      <select id="pid"><option selected value="4">新聞透視</option></select>
      <select id="eYear"><option selected value="2026">2026</option></select>
      <select id="eid"><option value="13274">2026-08-29 電子教「財」</option></select>
    </body></html>
    """

    def handler(request):
        return html_response(request, landing if request.url.path.endswith("evideo.php") else listing)

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.resources("videos")

    assert result["verified"] is True
    assert result["result_count"] == 1
    episode = result["resources"][0]
    assert episode["title"] == "2026-08-29 電子教「財」"
    assert episode["subjects"] == ["TVB Jade", "新聞透視"]
    assert "eid=13274" in episode["url"]
    assert result["scope"] == "selected_facet"
    assert result["facet"] == {
        "channel": {"value": "tvb", "title": "TVB Jade"},
        "programme": {"value": "4", "title": "新聞透視"},
        "year": {"value": "2026", "title": "2026"},
    }


def test_video_query_reads_validated_official_search_results():
    landing = """
    <div id="mainContent"><h1>eVideo Portal</h1>
      <iframe src="/web/english/eres2/evideo_embadded.php"></iframe>
    </div>
    """
    search = """
    <div id="search_result">
      <span>Found 2 results. Showing 1 to 2 records.</span>
      <table>
        <tr><td>一帶一路</td><td><a href="?keyword_search=一帶一路&amp;cid=tvb&amp;pid=272&amp;eYear=2017&amp;eid=6524&amp;start=0">責任擔當</a></td><td>2017-07-09</td></tr>
        <tr><td>財經透視</td><td><a href="?keyword_search=一帶一路&amp;cid=tvb&amp;pid=3&amp;eYear=2015&amp;eid=3508&amp;start=0">投資一帶一路股</a></td><td>2015-03-01</td></tr>
      </table>
    </div>
    """
    requests = []

    def handler(request):
        requests.append(request.url)
        return html_response(
            request,
            landing if request.url.path.endswith("evideo.php") else search,
        )

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.resources("videos", query="一帶一路")

    assert result["verified"] is True
    assert result["scope"] == "search_results"
    assert result["search"] == {"matched_count": 2, "shown_count": 2}
    assert result["result_count"] == 2
    assert [item["id"] for item in result["resources"]] == ["6524", "3508"]
    assert result["resources"][0]["programme_id"] == "272"
    assert requests[1].params["keyword_search"] == "一帶一路"
    assert requests[1].params["submit_search"] == "Search"


@pytest.mark.parametrize(
    ("search_body", "verified"),
    [
        (
            '<div id="search_result"><span>Found 0 results. Showing 1 to 0 records.</span><table></table></div>',
            True,
        ),
        ('<div id="search_result"><table></table></div>', False),
        (
            '<div id="search_result"><span>Found 1 results. Showing 1 to 1 records.</span><table></table></div>',
            False,
        ),
    ],
)
def test_video_query_only_accepts_official_zero_or_validated_table(search_body, verified):
    landing = """
    <div id="mainContent"><h1>eVideo Portal</h1>
      <iframe src="/web/english/eres2/evideo_embadded.php"></iframe>
    </div>
    """

    def handler(request):
        return html_response(
            request,
            landing if request.url.path.endswith("evideo.php") else search_body,
        )

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.resources("videos", query="missing")

    assert result["verified"] is verified
    assert result["resources"] == []


def test_guides_collection_reads_actual_subject_table():
    body = """
    <div id="mainContent"><h1>Subject Guide</h1>
      <table id="subjecttable">
        <tr><td><strong>Title</strong></td></tr>
        <tr><td><a href="https://libguides.vtc.edu.hk/design">Design</a>
          <ul><li><a href="https://libguides.vtc.edu.hk/architecture">Architecture</a></li></ul>
        </td></tr>
      </table>
    </div>
    """
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return html_response(request, body)

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.resources("guides")

    assert result["verified"] is True
    assert result["kind"] == "collection"
    assert requests == ["/web/english/guides/"]
    assert {item["title"] for item in result["resources"]} == {"Design", "Architecture"}
    assert all(item["external"] is False for item in result["resources"])


def test_database_query_keeps_server_matched_results_without_local_refiltering():
    landing = """
    <div id="mainContent"><h1>E-Databases</h1>
      <iframe src="https://libguides.vtc.edu.hk/az.php"></iframe>
    </div>
    """
    az_page = "<h1>A-Z Databases</h1><input id='site_id_default' value='7341'>"
    listing = """<div class="az-item">
      <h3><a class="az-title" href="/az/sciencedirect">ScienceDirect</a></h3>
      <div class="az-description-short">Science journals.</div>
    </div>"""

    def handler(request):
        if request.url.host == "library.vtc.edu.hk":
            return html_response(request, landing)
        if request.url.path == "/process/az/render-list":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"errText": "OK", "data": {"count": 1, "html": listing}},
                request=request,
            )
        return html_response(request, az_page)

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.resources("databases", query="no matching title")

    assert result["verified"] is True
    assert result["result_count"] == 1
    assert result["resources"][0]["title"] == "ScienceDirect"


def test_resources_rejects_unread_iframe_collection():
    landing = """
    <div id="mainContent"><h1>E-Databases</h1>
      <iframe src="https://libguides.vtc.edu.hk/az.php"></iframe>
    </div>
    """

    def handler(request):
        if request.url.host == "library.vtc.edu.hk":
            return html_response(request, landing)
        if request.url.path == "/process/az/render-list":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"errText": "OK", "data": {"count": 1, "html": "<p>Loading...</p>"}},
                request=request,
            )
        return html_response(
            request,
            "<h1>A-Z Databases</h1><input id='site_id_default' value='7341'>",
        )

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.resources("databases")

    assert result["verified"] is False
    assert result["resources"] == []
    assert result["error"] == "unexpected_page"


def test_database_collection_accepts_explicit_zero_result():
    landing = """
    <div id="mainContent"><h1>E-Databases</h1>
      <iframe src="https://libguides.vtc.edu.hk/az.php"></iframe>
    </div>
    """

    def handler(request):
        if request.url.host == "library.vtc.edu.hk":
            return html_response(request, landing)
        if request.url.path == "/process/az/render-list":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "errText": "OK",
                    "data": {
                        "count": 0,
                        "html": '<div class="s-lg-az-no-results">No databases found</div>',
                    },
                },
                request=request,
            )
        return html_response(
            request,
            "<h1>A-Z Databases</h1><input id='site_id_default' value='7341'>",
        )

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.resources("databases", query="nonexistent")

    assert result["verified"] is True
    assert result["result_count"] == 0
    assert result["resources"] == []


def test_database_collection_rejects_server_count_card_mismatch():
    landing = """
    <div id="mainContent"><h1>E-Databases</h1>
      <iframe src="https://libguides.vtc.edu.hk/az.php"></iframe>
    </div>
    """
    listing = '<div class="az-item"><a class="az-title" href="/az/one">One</a></div>'

    def handler(request):
        if request.url.host == "library.vtc.edu.hk":
            return html_response(request, landing)
        if request.url.path == "/process/az/render-list":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"errText": "OK", "data": {"count": 2, "html": listing}},
                request=request,
            )
        return html_response(
            request,
            "<h1>A-Z Databases</h1><input id='site_id_default' value='7341'>",
        )

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.resources("databases")

    assert result["verified"] is False
    assert result["error"] == "unexpected_page"


def test_info_extracts_text_links_and_dynamic_hours_table():
    body = """
    <html><body><div id="mainContent">
      <h1>Opening Hours</h1><p>Term Time</p>
      <script>$weekday = "9:00 am - 9:00 pm";</script>
      <table><tr><th>Campus</th><th>Monday - Friday</th></tr>
        <tr><td><a href="https://campus.example/">Chai Wan</a></td>
            <td><script>document.write($weekday);</script></td></tr>
      </table>
    </div></body></html>
    """

    with LibraryPublicClient(
        transport=httpx.MockTransport(lambda request: html_response(request, body))
    ) as client:
        result = client.info("hours")

    assert result["verified"] is True
    assert result["title"] == "Opening Hours"
    assert "9:00 am - 9:00 pm" in result["text"]
    assert result["tables"] == [
        {
            "caption": "Opening Hours",
            "headers": ["Campus", "Monday - Friday"],
            "rows": [["Chai Wan", "9:00 am - 9:00 pm"]],
        }
    ]
    assert result["links"][0]["external"] is True


@pytest.mark.parametrize(
    ("topic", "path", "heading"),
    [
        ("room-booking", "/web/english/roombook.php", "Book a Room"),
        ("laptop-loans", "/web/english/info/laptop-loan.php", "Laptop Loans"),
        ("forms", "/web/english/forms/forms.php", "Forms"),
        ("staff", "/web/english/info/staff.php", "Staff Directory"),
        ("alumni", "/web/english/info/as.php", "Alumni Services"),
    ],
)
def test_info_supports_additional_official_topics(topic, path, heading):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return html_response(
            request,
            f'<div id="mainContent"><h1>{heading}</h1><p>Official information.</p></div>',
        )

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.info(topic)

    assert result["verified"] is True
    assert result["title"] == heading
    assert requests == [path]


@pytest.mark.parametrize(
    "body",
    [
        "<html><title>VTC Library Services</title><body></body></html>",
        "<div id='mainContent'><h1>Maintenance</h1><p>Service unavailable</p></div>",
        "<div id='mainContent'><h1>Opening Hours</h1></div>",
    ],
)
def test_info_rejects_empty_malformed_or_maintenance_pages(body):
    with LibraryPublicClient(
        transport=httpx.MockTransport(lambda request: html_response(request, body))
    ) as client:
        result = client.info("hours")

    assert result["verified"] is False
    assert result["text"] == ""
    assert result["error"] == "unexpected_page"


def test_non_html_response_is_unverified():
    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"status": "ok"},
            request=request,
        )

    with LibraryPublicClient(transport=httpx.MockTransport(handler)) as client:
        result = client.links()

    assert result["verified"] is False
    assert result["error"] == "unexpected_content_type"
