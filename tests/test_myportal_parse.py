from datetime import date
from pathlib import Path

from vtc.myportal.parse import (
    classify_document,
    filter_documents,
    looks_like_myportal_login,
    match_activity,
    match_module,
    parse_html_tables,
    parse_timetable_cell,
    parse_timetable_grid,
    select_week_option,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_select_week_option_picks_containing_week():
    options = [
        {"value": "1", "label": "(48) 26-Jul-2026 - 01-Aug-2026"},
        {"value": "2", "label": "(49) 02-Aug-2026 - 08-Aug-2026"},
    ]
    chosen, _ = select_week_option(options, today=date(2026, 8, 3))
    assert chosen is not None
    assert chosen["week_no"] == "49"
    assert chosen["start"] == "2026-08-02"


def test_parse_timetable_grid_reads_rowspan_cells():
    cell = "ITP3902\nLecture (09:00 - 11:00)\nST-301\nCHAN, Tai Man\nWk:3"
    parsed = parse_timetable_cell(cell, "星期一", "(3) 14-Sep-2026 - 20-Sep-2026")
    assert parsed is not None
    assert parsed["weekday"] == "Monday"
    assert parsed["course"] == "ITP3902"
    assert parsed["time_range"] == "09:00-11:00"
    assert parsed["room"] == "ST-301"

    grid = [
        [
            {"text": "", "rowspan": 1},
            {"text": "Monday", "rowspan": 1},
            {"text": "Tuesday", "rowspan": 1},
        ],
        [
            {"text": "09:00", "rowspan": 1},
            {"text": cell, "rowspan": 2},
            {"text": "", "rowspan": 1},
        ],
        [
            {"text": "09:30", "rowspan": 1},
            {"text": "", "rowspan": 1},
        ],
    ]
    entries, weekdays = parse_timetable_grid(grid, "(3) 14-Sep-2026 - 20-Sep-2026")
    assert weekdays == ["Monday", "Tuesday"]
    assert len(entries) == 1
    assert entries[0]["instructor"] == "CHAN, Tai Man"


def test_parse_activity_and_module_tables():
    html = (FIXTURES / "myportal_tables.html").read_text(encoding="utf-8")
    records = parse_html_tables(html, base_url="https://myportal.vtc.edu.hk/")
    activities = [row for row in records if "orientation" in row["title"].lower()]
    modules = [row for row in records if row.get("code") == "ITP3902"]
    assert activities[0]["id"]
    assert match_activity(activities, activities[0]["id"])["title"] == "Orientation Day"
    assert match_module(modules, "ITP3902")["title"] == "Programming"


def test_classify_transcript_and_tuition_documents():
    html = (FIXTURES / "myportal_tables.html").read_text(encoding="utf-8")
    records = parse_html_tables(html)
    transcripts = filter_documents(records, "transcript")
    tuition = filter_documents(records, "tuition")
    assert classify_document("學業成績證明書") == "transcript"
    assert classify_document("學費繳費通知書") == "tuition"
    assert transcripts[0]["title"] == "學業成績證明書"
    assert tuition[0]["title"] == "學費繳費通知書"


def test_login_page_detection_needs_userid_and_password_fields():
    login_html = '<input name="userid"><input name="password">'
    home_html = "<a>Timetable</a><a>Log Out</a>"
    assert looks_like_myportal_login(login_html, "https://myportal.vtc.edu.hk/wps/portal")
    assert not looks_like_myportal_login(home_html, "https://myportal.vtc.edu.hk/wps/myportal/sp/")
