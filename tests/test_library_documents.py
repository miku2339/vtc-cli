import shutil

from vtc.library.documents import (
    LibraryDocumentsClient,
    _normalize_options,
    _parse_results,
    _safe_pdf_filename,
    _valid_pdf,
    _validate_output_path,
    _verified_select_component,
    verified_database_page,
    verified_results_page,
)


class FakeResponse:
    ok = True


class FakePage:
    def __init__(self, url, html):
        self.url = url
        self.html = html
        self.visited = []

    def content(self):
        return self.html

    def goto(self, url, **kwargs):
        self.url = url
        self.visited.append((url, kwargs))
        return FakeResponse()

    def locator(self, selector):
        return WaitingLocator()


class WaitingLocator:
    def wait_for(self, **kwargs):
        return None


AUTHENTICATED_HTML = """
<html><head><title>VTC Exam &amp; Syllabus Database System</title></head><body>
  <main><h1>Search Exams</h1>
    <form><input id="keyword" type="text" wire:model="keyword">
      <button type="submit">Submit</button><button type="button">Clear</button>
    </form>
  </main>
</body></html>
"""


def test_verified_database_page_requires_authenticated_form_and_expected_host():
    page = FakePage("https://vtclib23.vtc.edu.hk/exam/search", AUTHENTICATED_HTML)

    assert verified_database_page(page, "papers") is True

    page.url = "https://attacker.example/exam/search"
    assert verified_database_page(page, "papers") is False


def test_verified_database_page_rejects_login_form_even_on_search_path():
    page = FakePage(
        "https://vtclib23.vtc.edu.hk/exam/search",
        """
        <title>Login - VTC Exam &amp; Syllabus Database System</title>
        <main><h1>Sign in</h1><form>
          <input name="AuthState" value="private">
          <input name="sourceChoice[vtc-student-bridge]" type="submit">
        </form></main>
        """,
    )

    assert verified_database_page(page, "papers") is False


def test_verified_database_page_ignores_hidden_submit_loading_text():
    page = FakePage(
        "https://vtclib23.vtc.edu.hk/exam/search",
        AUTHENTICATED_HTML.replace(
            "Submit</button>",
            "Submit <span wire:loading>Uploading file...</span></button>",
        ),
    )

    assert verified_database_page(page, "papers") is True


def test_status_reads_the_authenticated_search_page():
    page = FakePage("about:blank", AUTHENTICATED_HTML)

    with LibraryDocumentsClient("papers", page=page) as client:
        result = client.status()

    assert result == {
        "verified": True,
        "source": "library_html",
        "service": "papers",
        "url": "https://vtclib23.vtc.edu.hk/exam/search",
        "error": None,
        "authenticated": True,
    }
    assert page.visited[0][0] == "https://vtclib23.vtc.edu.hk/exam/search"


def test_status_rejects_wrong_service_without_opening_a_page():
    page = FakePage("about:blank", AUTHENTICATED_HTML)

    result = LibraryDocumentsClient("unknown", page=page).status()

    assert result["verified"] is False
    assert result["error"] == "invalid_service"


def test_status_reports_missing_session_without_starting_browser(tmp_path):
    result = LibraryDocumentsClient(
        "papers",
        session_file=tmp_path / "missing.storage.json",
    ).status()

    assert result["verified"] is False
    assert result["error"] == "missing_session"


RESULTS_HTML = """
<html><head><title>VTC Exam &amp; Syllabus Database System</title></head><body>
  <main><h1>Exams</h1><h2>Filters</h2>
    <table><thead><tr>
      <th>Full Text</th><th>Academic Year</th><th>Session</th>
      <th>Semester / Level / Year</th><th>Department</th><th>Course</th>
      <th>Subject Code</th><th>Subject Title</th><th>Remarks</th>
    </tr></thead><tbody>
      <tr wire:key="abc.table.records.66993">
        <td><button wire:click="callTableColumnAction('file_path', '66993')">PDF</button></td>
        <td>2012/2013</td><td>Semester (Spring)</td><td>Semester 2</td>
        <td>ICT\nInformation and Communications Technology\n資訊及通訊科技</td>
        <td>IT114105\nHD in Software Engineering\n軟件工程高級文憑</td>
        <td>ITP3902</td><td>Discrete Mathematics and Statistics</td>
        <td>Common Paper</td>
      </tr>
    </tbody></table>
    <nav class="fi-pagination">Showing 1 to 1 of 1 results</nav>
  </main>
</body></html>
"""

SYLLABUS_RESULTS_HTML = """
<html><head><title>VTC Exam &amp; Syllabus Database System</title></head><body>
  <main><h1>Syllabi</h1><h2>Filters</h2>
    <table><thead><tr>
      <th>Full Text</th><th>Issuing Year</th><th>Course Code</th>
      <th>Course Title</th><th>Subject Code</th><th>Subject Title</th>
      <th>Discipline</th><th>Department</th><th>Remarks</th>
    </tr></thead><tbody>
      <tr wire:key="abc.table.records.9657">
        <td><button wire:click="callTableColumnAction('file_path', '9657')">PDF</button></td>
        <td>2025/2026</td><td>IT114105</td><td>HD in Software Engineering</td>
        <td>ITP3902</td><td>Discrete Mathematics and Statistics</td>
        <td>Information Technology</td><td>ICT</td><td></td>
      </tr>
    </tbody></table>
    <nav class="fi-pagination">Showing 1 to 1 of 24,939 results</nav>
  </main>
</body></html>
"""

EMPTY_SYLLABUS_HTML = """
<html><head><title>VTC Exam &amp; Syllabus Database System</title></head><body>
  <main><h1>Syllabi</h1><h2>Filters</h2>
    <div>Active filters</div><div>Keyword: ITP3902</div>
    <div class="fi-ta-empty-state">No Syllabi</div>
  </main>
</body></html>
"""


def test_verified_results_page_requires_exact_papers_schema():
    page = FakePage("https://vtclib23.vtc.edu.hk/exam/search/list", RESULTS_HTML)

    assert verified_results_page(page, "papers") is True

    page.html = RESULTS_HTML.replace("Subject Code", "Module Code")
    assert verified_results_page(page, "papers") is False


def test_parse_paper_results_binds_file_action_to_numeric_record_id():
    parsed = _parse_results(RESULTS_HTML, "papers", page=1, per_page=10)

    assert parsed is not None
    assert parsed["total"] == 1
    assert parsed["items"][0] == {
        "record_id": "66993",
        "full_text": True,
        "academic_year": "2012/2013",
        "session": "Semester (Spring)",
        "semester_level_year": "Semester 2",
        "department": "ICT Information and Communications Technology 資訊及通訊科技",
        "course": "IT114105 HD in Software Engineering 軟件工程高級文憑",
        "subject_code": "ITP3902",
        "subject_title": "Discrete Mathematics and Statistics",
        "remarks": "Common Paper",
    }


def test_parse_paper_results_rejects_mismatched_download_action():
    html = RESULTS_HTML.replace("file_path', '66993", "file_path', '12345")

    assert _parse_results(html, "papers", page=1, per_page=10) is None


def test_parse_syllabus_results_supports_comma_separated_total():
    row_start = SYLLABUS_RESULTS_HTML.index('      <tr wire:key="abc.table.records.9657">')
    row_end = SYLLABUS_RESULTS_HTML.index("      </tr>", row_start) + len("      </tr>")
    row = SYLLABUS_RESULTS_HTML[row_start:row_end]
    rows = "\n".join(row.replace("9657", str(9657 + index)) for index in range(10))
    html = SYLLABUS_RESULTS_HTML.replace(row, rows).replace(
        "Showing 1 to 1 of 24,939 results",
        "Showing 1 to 10 of 24,939 results",
    )
    page = FakePage(
        "https://vtclib23.vtc.edu.hk/syllabus/search/list",
        html,
    )

    assert verified_results_page(page, "syllabuses") is True
    parsed = _parse_results(html, "syllabuses", 1, 10)

    assert parsed is not None
    assert parsed["total"] == 24939
    assert parsed["items"][0] == {
        "record_id": "9657",
        "full_text": True,
        "issuing_year": "2025/2026",
        "course_code": "IT114105",
        "course_title": "HD in Software Engineering",
        "subject_code": "ITP3902",
        "subject_title": "Discrete Mathematics and Statistics",
        "discipline": "Information Technology",
        "department": "ICT",
        "remarks": "",
    }
    assert _parse_results(html, "syllabuses", 1, 100) is None


def test_parse_verified_empty_syllabus_result():
    page = FakePage(
        "https://vtclib23.vtc.edu.hk/syllabus/search/list?tableFilters%5Bkeyword%5D=x",
        EMPTY_SYLLABUS_HTML,
    )

    assert verified_results_page(page, "syllabuses") is True
    assert _parse_results(EMPTY_SYLLABUS_HTML, "syllabuses", 1, 10) == {
        "items": [],
        "documents": [],
        "total": 0,
    }


def test_empty_state_rejects_wrong_service_label_or_table():
    page = FakePage(
        "https://vtclib23.vtc.edu.hk/syllabus/search/list",
        EMPTY_SYLLABUS_HTML.replace("No Syllabi", "No Exams"),
    )

    assert verified_results_page(page, "syllabuses") is False
    assert _parse_results(page.html, "syllabuses", 1, 10) is None


def test_select_component_only_accepts_fixed_field_livewire_methods():
    x_data = """
    selectFormComponent({
      getOptionsUsing: async () => await $wire.getFormSelectOptions('year'),
      getSearchResultsUsing: async (search) => await $wire.getFormSelectSearchResults('year', search),
      statePath: 'year', options: JSON.parse('[]')
    })
    """

    assert _verified_select_component(x_data, "year") is True
    assert _verified_select_component(x_data, "session") is False


def test_normalize_filter_options_preserves_server_values_and_disabled_state():
    options = _normalize_options(
        [
            {"label": "2026/2027", "value": 2026, "disabled": False},
            {"label": "Others", "value": "5", "disabled": True},
        ]
    )

    assert options == [
        {"label": "2026/2027", "value": "2026", "disabled": False},
        {"label": "Others", "value": "5", "disabled": True},
    ]


def test_current_per_page_reads_visible_persisted_selection():
    class Select:
        def is_visible(self):
            return True

        def input_value(self):
            return "20"

    class Page:
        def locator(self, selector):
            return LocatorList([Select()])

    client = LibraryDocumentsClient("papers", page=Page())

    assert client._current_per_page(client._page) == 20


def test_pdf_output_validation_rejects_overwrite_and_non_pdf(tmp_path):
    output = tmp_path / "paper.pdf"

    assert _validate_output_path(output) is None
    assert _validate_output_path(tmp_path / "paper.txt") == "invalid_output"
    output.write_bytes(b"%PDF-test")
    assert _validate_output_path(output) == "output_exists"
    assert _valid_pdf(output) is True
    assert _safe_pdf_filename("paper.pdf") is True
    assert _safe_pdf_filename("../paper.pdf") is False


class DownloadButton:
    def __init__(self, action, on_click=None):
        self.action = action
        self.on_click = on_click
        self.clicked = False

    def get_attribute(self, name):
        return self.action if name == "wire:click" else None

    def click(self):
        self.clicked = True
        if self.on_click is not None:
            self.on_click()

    def is_visible(self):
        return True

    def is_disabled(self):
        return False


class LocatorList:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class DownloadRow:
    def __init__(self, record_id, button):
        self.record_id = record_id
        self.button = button

    def get_attribute(self, name):
        return f"abc.table.records.{self.record_id}" if name == "wire:key" else None

    def locator(self, selector):
        return LocatorList([self.button])


class FakeDownload:
    suggested_filename = "syllabus.pdf"

    def __init__(self, source):
        self.source = source

    def failure(self):
        return None

    def path(self):
        return self.source

    def save_as(self, output):
        shutil.copyfile(self.source, output)


class DownloadInfo:
    def __init__(self, download):
        self.value = download

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class DownloadPage(FakePage):
    def __init__(self, html, record_id, download):
        super().__init__("https://vtclib23.vtc.edu.hk/syllabus/search/list", html)
        self.button = DownloadButton(
            f"callTableColumnAction('file_path', '{record_id}')"
        )
        self.row = DownloadRow(record_id, self.button)
        self.download = download
        self.next_button = DownloadButton("nextPage('page')", self._next_page)

    def _next_page(self):
        self.html = self.html.replace(
            "Showing 1 to 1 of 24,939 results",
            "Showing 101 to 101 of 101 results",
        )

    def locator(self, selector):
        if selector == "main table tbody tr[wire\\:key]":
            return LocatorList([self.row])
        if selector == ".fi-pagination button[aria-label='Next']":
            return LocatorList([self.next_button])
        return WaitingLocator()

    def wait_for_function(self, *args, **kwargs):
        return True

    def expect_download(self, **kwargs):
        return DownloadInfo(self.download)


def test_download_finds_record_after_first_page_and_writes_verified_pdf(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test payload")
    page = DownloadPage(SYLLABUS_RESULTS_HTML, "9657", FakeDownload(source))
    client = LibraryDocumentsClient("syllabuses", page=page)
    requested_pages = []

    def search(query, *, page, per_page):
        requested_pages.append(page)
        return {
            "verified": True,
            "items": [{"record_id": "1"}],
            "total": 101,
        }

    client.search = search
    output = tmp_path / "saved.pdf"

    result = client.download("9657", output)

    assert result["verified"] is True
    assert result["path"] == str(output)
    assert result["bytes"] == len(b"%PDF-test payload")
    assert requested_pages == [1]
    assert page.next_button.clicked is True
    assert page.button.clicked is True
    assert output.read_bytes() == b"%PDF-test payload"
    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())


def test_download_directory_uses_safe_suggested_filename(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-directory")
    page = DownloadPage(SYLLABUS_RESULTS_HTML, "9657", FakeDownload(source))
    client = LibraryDocumentsClient("syllabuses", page=page)
    client.search = lambda query, *, page, per_page: {
        "verified": True,
        "items": [{"record_id": "9657"}],
        "total": 1,
    }
    output = tmp_path / "library-files"

    result = client.download("9657", output)

    assert result["verified"] is True
    assert result["path"] == str(output / "syllabus.pdf")
    assert (output / "syllabus.pdf").read_bytes() == b"%PDF-directory"
