from pathlib import Path

from vtc.moodle.html import (
    extract_course_code,
    is_syncable_modtype,
    parse_assignment_page,
    parse_course_list,
    parse_course_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_course_page_extracts_code_and_syncable_activities():
    html = (FIXTURES / "course.html").read_text(encoding="utf-8")
    parsed = parse_course_page(html, "https://moodle2526.vtc.edu.hk/course/view.php?id=77")
    assert parsed["course_code"] == "ITP3902"
    assert parsed["course_id"] == 77
    types = {item["modtype"] for item in parsed["activities"]}
    assert types == {"resource", "folder", "assign", "lti"}
    syncable = [item for item in parsed["activities"] if is_syncable_modtype(item["modtype"])]
    assert [item["title"] for item in syncable] == ["Week 1 slides", "Tutorial notes", "Assignment 1"]
    assert all("/mod/" in (item["url"] or "") for item in syncable)


def test_parse_course_list_deduplicates_and_reads_codes():
    html = (FIXTURES / "courses.html").read_text(encoding="utf-8")
    rows = parse_course_list(html)
    assert [row["id"] for row in rows] == [101, 202]
    assert rows[0]["code"] == "ITP3902"
    assert rows[1]["code"] == "LAN4103"


def test_parse_assignment_page_keeps_due_date_and_does_not_claim_empty():
    html = (FIXTURES / "assignment.html").read_text(encoding="utf-8")
    parsed = parse_assignment_page(html, "https://moodle2526.vtc.edu.hk/mod/assign/view.php?id=21")
    assert parsed["due"] == "Monday, 15 September 2026, 11:59 PM"
    assert parsed["opened"].startswith("Monday")
    assert extract_course_code("LAN4103 English") == "LAN4103"
