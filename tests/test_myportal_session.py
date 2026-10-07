import sys
from pathlib import Path
from types import SimpleNamespace

from vtc.myportal import session as session_module
from vtc.myportal.session import MyPortalSession


def bare_session(page):
    client = object.__new__(MyPortalSession)
    client.page = page
    client.timeout_ms = 100
    return client


class EmptyRows:
    @property
    def first(self):
        return self

    def count(self):
        return 0

    def nth(self, index):
        raise IndexError(index)


class EmptyPage:
    url = "https://myportal.vtc.edu.hk/wps/myportal/sp/"
    frames = []

    def get_by_role(self, *args, **kwargs):
        return EmptyRows()

    def get_by_text(self, *args, **kwargs):
        return EmptyRows()

    def locator(self, selector):
        return EmptyRows()


def test_browser_context_keeps_tls_verification_enabled(monkeypatch, tmp_path):
    captured = {}

    class Context:
        def new_page(self):
            return EmptyPage()

    class Browser:
        def new_context(self, **kwargs):
            captured.update(kwargs)
            return Context()

    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=lambda **kwargs: Browser())
    )
    runner = SimpleNamespace(start=lambda: playwright)
    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=lambda: runner),
    )
    client = bare_session(None)
    client.session_file = tmp_path / "session.json"
    client.headed = False
    client._playwright = None
    client._browser = None
    client._context = None
    monkeypatch.setattr(client, "_open_home", lambda: None)

    client.__enter__()

    assert "ignore_https_errors" not in captured


def test_documents_filters_records_without_name_error(monkeypatch):
    page = EmptyPage()
    page.url = "https://swsdownload.vtc.edu.hk/swsdownload/"
    client = bare_session(page)
    monkeypatch.setattr(
        client,
        "open_section",
        lambda section: {"clicked": True, "verified": True},
    )
    monkeypatch.setattr(
        client,
        "_page_records",
        lambda: [{"title": "學業成績證明書", "url": "/transcript.pdf"}],
    )
    monkeypatch.setattr(client, "nav_labels", lambda: [])

    result = client.documents("transcript")

    assert [item["title"] for item in result["documents"]] == ["學業成績證明書"]


class WeekOptions:
    def evaluate_all(self, script):
        return [{"value": "from", "label": "(3) 14-Sep-2026 - 20-Sep-2026"}]


class WeekSelect:
    def __init__(self):
        self.selected = []

    def locator(self, selector):
        assert selector == "option"
        return WeekOptions()

    def select_option(self, value):
        self.selected.append(value)


class TimetablePage(EmptyPage):
    def wait_for_timeout(self, timeout):
        pass


def test_timetable_without_search_click_is_unverified(monkeypatch):
    client = bare_session(TimetablePage())
    week_select = WeekSelect()
    selectors = []

    def first_locator(selector):
        selectors.append(selector)
        if "DateFrom" in selector:
            return week_select
        return None

    monkeypatch.setattr(client, "open_section", lambda section: {})
    monkeypatch.setattr(client, "_first_locator", first_locator)
    monkeypatch.setattr(client, "_click_pattern", lambda patterns: False)
    monkeypatch.setattr(client, "_wait_quiet", lambda: None)
    monkeypatch.setattr(client, "nav_labels", lambda: [])

    result = client.timetable()

    assert result["found"] is False
    assert result["evidence"]["search_clicked"] is False
    assert all("select[name*='beanDate']" not in selector for selector in selectors)


def test_timetable_without_readable_grid_is_unverified(monkeypatch):
    client = bare_session(TimetablePage())
    week_select = WeekSelect()

    def first_locator(selector):
        if "DateFrom" in selector:
            return week_select
        return None

    monkeypatch.setattr(client, "open_section", lambda section: {})
    monkeypatch.setattr(client, "_first_locator", first_locator)
    monkeypatch.setattr(client, "_click_pattern", lambda patterns: True)
    monkeypatch.setattr(client, "_wait_quiet", lambda: None)
    monkeypatch.setattr(client, "nav_labels", lambda: [])

    result = client.timetable()

    assert result["found"] is False
    assert result["evidence"]["search_clicked"] is True
    assert "unverified" in result["details"].lower()


class FakeResponse:
    def __init__(self, status):
        self.status = status


class RoutePage(EmptyPage):
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.visited = []

    def goto(self, url, **kwargs):
        final_url, status = next(self.outcomes)
        self.visited.append(url)
        self.url = final_url
        return FakeResponse(status)

    def content(self):
        return "<html><body>Section</body></html>"


def test_open_section_skips_error_route_and_uses_next_candidate(monkeypatch):
    paths = (
        "https://myportal.vtc.edu.hk/wps/myportal/sp/timetable/bad/",
        "https://myportal.vtc.edu.hk/wps/myportal/sp/timetable/good/",
    )
    monkeypatch.setitem(session_module.SECTION_PATHS, "timetable", paths)
    page = RoutePage([(paths[0], 404), (paths[1], 200)])
    client = bare_session(page)
    monkeypatch.setattr(client, "_wait_quiet", lambda: None)
    monkeypatch.setattr(client, "_session_expired", lambda: False)

    evidence = client.open_section("timetable")

    assert page.visited == list(paths)
    assert evidence["path"] == paths[1]


def test_section_route_rejects_http_200_guess_that_is_not_real_section():
    guessed = "https://myportal.vtc.edu.hk/wps/myportal/sp/student_activity/"
    page = RoutePage([(guessed, 200)])
    client = bare_session(page)
    response = page.goto(guessed)

    assert client._section_route_loaded("activities", guessed, response) is False


class SectionLink:
    def __init__(self, page, label, href, *, visible=True):
        self.page = page
        self.label = label
        self.href = href
        self.visible = visible
        self.clicked = False

    def inner_text(self):
        return self.label

    def get_attribute(self, name):
        assert name == "href"
        return self.href

    def is_visible(self):
        return self.visible

    def click(self, **kwargs):
        self.clicked = True
        self.page.url = f"https://myportal.vtc.edu.hk{self.href}"


class SectionPage(EmptyPage):
    def __init__(self):
        self.url = "https://myportal.vtc.edu.hk/wps/myportal/sp/"
        self.frames = []
        self.links = [
            SectionLink(
                self,
                "Student Activity",
                "/wps/myportal/sp/actstud/!ut/p/example",
            )
        ]

    def locator(self, selector):
        assert selector == "a[href]"
        return DownloadAnchors(self.links)

    def content(self):
        return "<html><body><h2>Student Activity</h2></body></html>"

    def goto(self, *args, **kwargs):
        raise AssertionError("guessed route used before observed exact link")


def test_open_section_prefers_observed_exact_home_link(monkeypatch):
    page = SectionPage()
    client = bare_session(page)
    monkeypatch.setattr(client, "_wait_quiet", lambda: None)

    evidence = client.open_section("activities")

    assert page.links[0].clicked is True
    assert evidence["clicked"] is True
    assert "/sp/actstud/" in evidence["final_url"]


def test_open_section_chooses_visible_link_when_encoded_hrefs_differ(monkeypatch):
    page = SectionPage()
    page.links = [
        SectionLink(
            page,
            "Student Activity",
            "/wps/myportal/sp/actstud/!ut/p/desktop",
            visible=False,
        ),
        SectionLink(
            page,
            "Student Activity",
            "/wps/myportal/sp/actstud/!ut/p/mobile",
        ),
    ]
    client = bare_session(page)
    monkeypatch.setattr(client, "_wait_quiet", lambda: None)

    evidence = client.open_section("activities")

    assert page.links[0].clicked is False
    assert page.links[1].clicked is True
    assert evidence["verified"] is True


class PopupInfo:
    def __init__(self, page):
        self.value = page

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class PopupPage(EmptyPage):
    def __init__(self):
        self.url = "https://swsdownload.vtc.edu.hk/swsdownload/"
        self.frames = []

    def content(self):
        return "<html><body><h3>學業成績</h3></body></html>"


class PopupSectionLink(SectionLink):
    def click(self, **kwargs):
        self.clicked = True


class DocumentHomePage(SectionPage):
    def __init__(self):
        self.url = "https://myportal.vtc.edu.hk/wps/myportal/sp/"
        self.frames = []
        self.popup = PopupPage()
        self.links = [
            PopupSectionLink(
                self,
                "Document Download",
                "/wps/PA_StPortalWebService/TokenGen",
            )
        ]

    def expect_popup(self, **kwargs):
        return PopupInfo(self.popup)


def test_open_documents_adopts_trusted_swsdownload_popup(monkeypatch):
    page = DocumentHomePage()
    client = bare_session(page)
    client._portal_page = page
    monkeypatch.setattr(client, "_wait_quiet", lambda: None)

    evidence = client.open_section("documents")

    assert client.page is page.popup
    assert evidence["verified"] is True
    assert evidence["final_url"] == "https://swsdownload.vtc.edu.hk/swsdownload/"


class HtmlFrame:
    def __init__(self, url, html):
        self.url = url
        self._html = html

    def content(self):
        return self._html


class FramedPage(HtmlFrame):
    def __init__(self):
        super().__init__("https://myportal.vtc.edu.hk/home/", "<html></html>")
        self.frames = [
            HtmlFrame(
                "https://swsdownload.vtc.edu.hk/notices/index.html",
                '<a href="files/fees.pdf">學費繳費通知書</a>',
            )
        ]


def test_page_records_resolves_relative_links_against_frame_url():
    records = bare_session(FramedPage())._page_records()

    assert records[0]["url"] == "https://swsdownload.vtc.edu.hk/notices/files/fees.pdf"


def test_documents_parse_frame_links_even_when_frame_has_other_table(monkeypatch):
    page = FramedPage()
    page.url = "https://swsdownload.vtc.edu.hk/swsdownload/"
    page.frames[0]._html = """
        <table>
          <tr><th>Title</th><th>Status</th></tr>
          <tr><td>Other service</td><td>Open</td></tr>
        </table>
        <a href="files/transcript.pdf">學業成績證明書</a>
    """
    client = bare_session(page)
    monkeypatch.setattr(
        client,
        "open_section",
        lambda section: {"clicked": True, "verified": True},
    )
    monkeypatch.setattr(client, "nav_labels", lambda: [])

    result = client.documents("transcript")

    assert result["documents"][0]["url"] == (
        "https://swsdownload.vtc.edu.hk/notices/files/transcript.pdf"
    )


def test_activities_reject_home_navigation_and_guide_links(monkeypatch):
    page = FramedPage()
    page.url = "https://myportal.vtc.edu.hk/wps/myportal/sp/"
    client = bare_session(page)
    monkeypatch.setattr(client, "open_section", lambda section: {"clicked": False})
    monkeypatch.setattr(
        client,
        "_page_records",
        lambda: [
            {"title": "OMS Guide", "url": "https://example.test/oms-guide.pdf"},
            {"title": "Document Download", "url": "https://example.test/download"},
        ],
    )
    monkeypatch.setattr(client, "nav_labels", lambda: [])

    result = client.activities()

    assert result["found"] is False
    assert result["activities"] == []


def test_documents_do_not_parse_portal_home_navigation(monkeypatch):
    page = FramedPage()
    page.url = "https://myportal.vtc.edu.hk/wps/myportal/sp/"
    client = bare_session(page)
    monkeypatch.setattr(
        client,
        "open_section",
        lambda section: {"clicked": False, "verified": False},
    )
    monkeypatch.setattr(
        client,
        "_page_records",
        lambda: [
            {"title": "學費減免計劃申請結果通知", "url": "https://example.test/fee"}
        ],
    )
    monkeypatch.setattr(client, "nav_labels", lambda: [])

    result = client.documents("tuition")

    assert result["found"] is False
    assert result["documents"] == []
    assert result["all_count"] == 0


class ModalControl:
    def __init__(self, *, title=None, on_click=None):
        self.title = title
        self.on_click = on_click
        self.clicked = False

    def is_visible(self):
        return True

    def get_attribute(self, name):
        assert name == "title"
        return self.title

    def click(self, **kwargs):
        self.clicked = True
        if self.on_click:
            self.on_click()


class ModalControls:
    def __init__(self, controls):
        self.controls = controls

    def count(self):
        return len(self.controls)

    @property
    def first(self):
        return self

    def nth(self, index):
        return self.controls[index]

    def wait_for(self, **kwargs):
        if not self.controls:
            raise TimeoutError("no visible file control")


class DocumentServicePage(EmptyPage):
    def __init__(self, file_titles):
        self.url = "https://swsdownload.vtc.edu.hk/swsdownload/"
        self.frames = []
        self.modal_open = False
        self.files = [ModalControl(title=title) for title in file_titles]
        self.card = ModalControl(on_click=lambda: setattr(self, "modal_open", True))

    def locator(self, selector):
        if "input[type='hidden']" in selector or "input[type=hidden]" in selector:
            raise AssertionError("hidden download metadata must not be read")
        if selector == "#transcript button.downloadBtn":
            return ModalControls([self.card])
        if selector == "#paymentAdvice button.downloadBtn":
            return ModalControls([])
        if selector == "#download_div.showDialog a.fileDownloadBtn.modal_box_btn.primary":
            return ModalControls(self.files if self.modal_open else [])
        return ModalControls([])


def test_documents_open_exact_card_and_parse_visible_modal_file_titles(monkeypatch):
    title = "STUDENT_transcript.pdf (07-Oct-2026)"
    page = DocumentServicePage([title])
    client = bare_session(page)
    monkeypatch.setattr(
        client,
        "open_section",
        lambda section: {"clicked": True, "verified": True},
    )

    result = client.documents("transcript")

    assert page.card.clicked is True
    assert result["found"] is True
    assert result["documents"] == [
        {
            "id": session_module.item_id("transcript", title),
            "title": title,
            "kind": "transcript",
        }
    ]


def test_documents_do_not_treat_available_status_as_a_file(monkeypatch):
    page = DocumentServicePage([])
    client = bare_session(page)
    monkeypatch.setattr(
        client,
        "open_section",
        lambda section: {"clicked": True, "verified": True},
    )

    result = client.documents("transcript")

    assert result["found"] is False
    assert result["documents"] == []


def test_modules_period_information_is_not_a_module_record(monkeypatch):
    page = FramedPage()
    page.url = "https://myportal.vtc.edu.hk/wps/myportal/sp/omsstud/!ut/p/example"
    client = bare_session(page)
    monkeypatch.setattr(client, "open_section", lambda section: {"clicked": True})
    monkeypatch.setattr(
        client,
        "_page_records",
        lambda: [
            {
                "title": "15-Sep-2026 20:00 - 16-Sep-2026 08:00",
                "cells": {"Module selection period": "15-Sep-2026 20:00"},
            }
        ],
    )
    monkeypatch.setattr(client, "nav_labels", lambda: [])

    result = client.modules()

    assert result["found"] is False
    assert result["modules"] == []


class DownloadAnchor:
    def __init__(self, href):
        self.href = href
        self.clicked = False

    def get_attribute(self, name):
        assert name == "href"
        return self.href

    def click(self):
        self.clicked = True


class DownloadAnchors:
    def __init__(self, anchors):
        self.anchors = anchors

    def count(self):
        return len(self.anchors)

    def nth(self, index):
        return self.anchors[index]


class DownloadFrame(HtmlFrame):
    def __init__(self):
        super().__init__("https://swsdownload.vtc.edu.hk/notices/index.html", "")
        self.anchors = [
            DownloadAnchor("files/other.pdf"),
            DownloadAnchor("files/transcript.pdf"),
        ]

    def locator(self, selector):
        assert selector == "a[href]"
        return DownloadAnchors(self.anchors)


class FakeDownload:
    suggested_filename = "transcript.pdf"

    def __init__(self):
        self.saved_as = None

    def save_as(self, path):
        self.saved_as = path


class DownloadPending:
    def __init__(self, download):
        self.value = download

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class DownloadPage(EmptyPage):
    def __init__(self):
        self.frame = DownloadFrame()
        self.frames = [self.frame]
        self.download = FakeDownload()

    def expect_download(self, **kwargs):
        return DownloadPending(self.download)


def test_download_clicks_exact_anchor_inside_owning_frame(monkeypatch, tmp_path):
    page = DownloadPage()
    client = bare_session(page)
    href = "https://swsdownload.vtc.edu.hk/notices/files/transcript.pdf"
    monkeypatch.setattr(
        client,
        "documents",
        lambda kind: {"documents": [{"id": "transcript-1", "url": href}]},
    )

    result = client.download_documents("transcript", tmp_path)

    assert page.frame.anchors[0].clicked is False
    assert page.frame.anchors[1].clicked is True
    assert result["documents"][0]["path"] == str(tmp_path / "transcript.pdf")


class ModalDownloadPage(DocumentServicePage):
    def __init__(self, title):
        super().__init__([title])
        self.modal_open = True
        self.download = FakeDownload()

    def expect_download(self, **kwargs):
        return DownloadPending(self.download)


def test_download_clicks_exact_visible_modal_file_control(monkeypatch, tmp_path):
    title = "STUDENT_transcript.pdf (07-Oct-2026)"
    page = ModalDownloadPage(title)
    client = bare_session(page)
    monkeypatch.setattr(
        client,
        "documents",
        lambda kind: {
            "documents": [
                {
                    "id": session_module.item_id("transcript", title),
                    "title": title,
                    "kind": "transcript",
                }
            ]
        },
    )

    result = client.download_documents("transcript", tmp_path)

    assert page.files[0].clicked is True
    assert result["documents"][0]["path"] == str(tmp_path / "transcript.pdf")


def test_download_destination_is_stable_and_never_overwrites(tmp_path):
    client = bare_session(EmptyPage())
    existing = tmp_path / "statement.pdf"
    existing.write_bytes(b"keep")
    item = {"id": "tuition-statement-1234567890"}

    destination = client._download_destination(tmp_path, "statement.pdf", item, set())

    assert destination != existing
    assert destination.name == "statement-tuition-statement-1234567890.pdf"
    assert existing.read_bytes() == b"keep"


def test_click_row_action_does_not_use_global_fallback(monkeypatch):
    client = bare_session(EmptyPage())
    monkeypatch.setattr(
        client,
        "_click_pattern",
        lambda patterns: (_ for _ in ()).throw(AssertionError("global fallback used")),
    )

    assert client._click_row_action({"title": "Missing activity"}, "Apply") is False


class CellTexts:
    def __init__(self, texts):
        self.texts = texts

    def all_inner_texts(self):
        return self.texts


class ActionControls:
    def __init__(self):
        self.clicked = False

    def count(self):
        return 1

    def nth(self, index):
        assert index == 0
        return self

    def click(self, **kwargs):
        self.clicked = True


class ActionRow:
    def __init__(self, title):
        self.title = title
        self.control = ActionControls()

    def locator(self, selector):
        assert selector == "th, td"
        return CellTexts([self.title, "Apply"])

    def get_by_role(self, role, name=None):
        return self.control if role == "button" else EmptyRows()


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def count(self):
        return len(self.rows)

    def nth(self, index):
        return self.rows[index]


class DuplicateRowPage(EmptyPage):
    def __init__(self):
        self.rows = [ActionRow("Orientation Day"), ActionRow("Orientation Day")]

    def locator(self, selector):
        assert selector == "tr"
        return Rows(self.rows)


def test_click_row_action_rejects_ambiguous_exact_rows():
    page = DuplicateRowPage()
    client = bare_session(page)

    clicked = client._click_row_action({"title": "Orientation Day"}, "Apply")

    assert clicked is False
    assert not any(row.control.clicked for row in page.rows)


def test_clicked_activity_stays_unverified_without_success_marker(monkeypatch):
    client = bare_session(EmptyPage())
    record = {"id": "orientation-123", "title": "Orientation Day"}
    monkeypatch.setattr(
        client,
        "activities",
        lambda: {"activities": [record], "found": True},
    )
    monkeypatch.setattr(client, "_click_row_action", lambda record, button: True)

    result = client.apply_activity("orientation-123")

    assert result["applied"] is False
    assert result["action_clicked"] is True
    assert "unverified" in result["details"].lower()
