from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from vtc import __version__
from vtc.errors import MissingSession, VtcError, WriteBlocked
from vtc.paths import myportal_session_path, rest_status_path, session_path
from vtc.secrets import SECRET_ENV_NAMES, stdin_is_tty
from vtc.provenance import envelope
from vtc.sites import SITE_CHOICES


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 3
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 3
    try:
        payload = handler(args)
    except VtcError as exc:
        payload = envelope(
            command=getattr(args, "command_name", args.cmd),
            site=getattr(args, "site", None),
            source="unverified",
            ok=False,
            status=exc.status,
            error=str(exc),
        )
        _emit(payload, json_mode=_wants_json(args))
        return exc.exit_code
    except Exception as exc:
        payload = envelope(
            command=getattr(args, "command_name", "vtc"),
            site=getattr(args, "site", None),
            source="unverified",
            ok=False,
            status="error",
            error=exc.__class__.__name__,
        )
        _emit(payload, json_mode=_wants_json(args))
        return 1
    _emit(payload, json_mode=_wants_json(args))
    return 0 if payload.get("ok") else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vtc",
        description="Read-only VTC Moodle, MyPortal and Library CLI with local session login.",
    )
    parser.add_argument("--version", action="version", version=f"vtc {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    login = sub.add_parser("login", help="Store a local session using a terminal or private credential file.")
    login_sub = login.add_subparsers(dest="target", required=True)

    moodle_login = login_sub.add_parser("moodle", help="Sign in to VTC Moodle via Student SSO.")
    _add_site(moodle_login)
    moodle_login.add_argument("--json", action="store_true")
    moodle_login.add_argument("--headed", action="store_true", help="Show the browser window.")
    moodle_login.add_argument("--credentials-file", type=Path, help="Read account, password and optional TOTP seed from a private Markdown file.")
    moodle_login.add_argument(
        "--store-password",
        action="store_true",
        help="Save the password to macOS Keychain after it is read.",
    )
    moodle_login.add_argument(
        "--totp-auto",
        action="store_true",
        help="If the page asks for MFA, generate a code from a stored TOTP seed. Off by default.",
    )
    moodle_login.add_argument(
        "--store-totp",
        action="store_true",
        help="Save the TOTP seed to Keychain. Only useful with --totp-auto.",
    )
    moodle_login.set_defaults(handler=cmd_login_moodle, command_name="login.moodle")

    myportal_login = login_sub.add_parser("myportal", help="Sign in to VTC MyPortal.")
    myportal_login.add_argument("--json", action="store_true")
    myportal_login.add_argument("--headed", action="store_true", help="Show the browser window.")
    myportal_login.add_argument("--credentials-file", type=Path, help="Read account, password and optional TOTP seed from a private Markdown file.")
    myportal_login.add_argument(
        "--store-password",
        action="store_true",
        help="Save the password to macOS Keychain after it is read.",
    )
    myportal_login.set_defaults(handler=cmd_login_myportal, command_name="login.myportal")

    library_login = login_sub.add_parser("library", help="Sign in to a VTC Library service via CNA SSO.")
    library_login.add_argument("--service", choices=("catalogue", "papers", "syllabuses"), default="catalogue")
    library_login.add_argument("--credentials-file", type=Path)
    library_login.add_argument("--headed", action="store_true")
    library_login.add_argument("--json", action="store_true")
    library_login.set_defaults(handler=cmd_login_library, command_name="login.library")

    moodle = sub.add_parser("moodle", help="Read Moodle with a stored session.")
    moodle_sub = moodle.add_subparsers(dest="moodle_cmd", required=True)

    status = moodle_sub.add_parser("status", help="Check the local session without printing secrets.")
    _add_site(status)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=cmd_moodle_status, command_name="moodle.status")

    courses = moodle_sub.add_parser("courses", help="List enrolled courses.")
    _add_site(courses)
    courses.add_argument("--json", action="store_true")
    courses.set_defaults(handler=cmd_moodle_courses, command_name="moodle.courses")

    assignments = moodle_sub.add_parser("assignments", help="List published assignments and due dates.")
    _add_site(assignments)
    assignments.add_argument("--course")
    assignments.add_argument("--json", action="store_true")
    assignments.set_defaults(handler=cmd_moodle_assignments, command_name="moodle.assignments")

    sync = moodle_sub.add_parser("sync", help="Download lecture/tutorial/assignment files.")
    _add_site(sync)
    sync.add_argument("--course", required=True)
    sync.add_argument("--output", required=True, type=Path)
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--no-extract", action="store_true", help="Download files without reading PDF/Word/PPT text.")
    sync.add_argument("--json", action="store_true")
    sync.set_defaults(handler=cmd_moodle_sync, command_name="moodle.sync")

    extract = moodle_sub.add_parser(
        "extract",
        help="Read text from a downloaded PDF, Word, or PowerPoint file.",
    )
    extract.add_argument("--path", required=True, type=Path)
    extract.add_argument("--json", action="store_true")
    extract.set_defaults(handler=cmd_moodle_extract, command_name="moodle.extract")

    myportal = sub.add_parser("myportal", help="Read MyPortal with a stored session.")
    myportal_sub = myportal.add_subparsers(dest="myportal_cmd", required=True)

    mp_status = myportal_sub.add_parser("status", help="Check the local MyPortal session.")
    mp_status.add_argument("--json", action="store_true")
    mp_status.set_defaults(handler=cmd_myportal_status, command_name="myportal.status")

    timetable = myportal_sub.add_parser("timetable", help="Read class timetable.")
    timetable.add_argument("--today", action="store_true", help="Only return today's classes.")
    timetable.add_argument("--json", action="store_true")
    timetable.set_defaults(handler=cmd_myportal_timetable, command_name="myportal.timetable")

    activities = myportal_sub.add_parser("activities", help="List activity enrolment options.")
    activities.add_argument("--json", action="store_true")
    activities.set_defaults(handler=cmd_myportal_activities, command_name="myportal.activities")

    apply_p = myportal_sub.add_parser("apply", help="Register for an activity. Terminal only.")
    apply_p.add_argument("--id", required=True, help="Activity id from `vtc myportal activities`.")
    apply_p.add_argument("--confirm", action="store_true", help="Required. Actually submit the registration.")
    apply_p.add_argument("--json", action="store_true")
    apply_p.set_defaults(handler=cmd_myportal_apply, command_name="myportal.apply")

    modules = myportal_sub.add_parser("modules", help="List online module selection options.")
    modules.add_argument("--json", action="store_true")
    modules.set_defaults(handler=cmd_myportal_modules, command_name="myportal.modules")

    select_p = myportal_sub.add_parser("select", help="Select a module. Terminal only.")
    select_p.add_argument("--code", required=True, help="Module code from `vtc myportal modules`.")
    select_p.add_argument("--confirm", action="store_true", help="Required. Actually submit the selection.")
    select_p.add_argument("--json", action="store_true")
    select_p.set_defaults(handler=cmd_myportal_select, command_name="myportal.select")

    transcript = myportal_sub.add_parser("transcript", help="List or download academic transcript files.")
    transcript.add_argument("--output", type=Path, help="Download matching PDFs into this directory.")
    transcript.add_argument("--json", action="store_true")
    transcript.set_defaults(handler=cmd_myportal_transcript, command_name="myportal.transcript")

    tuition = myportal_sub.add_parser("tuition", help="List or download tuition fee notices.")
    tuition.add_argument("--output", type=Path, help="Download matching PDFs into this directory.")
    tuition.add_argument("--json", action="store_true")
    tuition.set_defaults(handler=cmd_myportal_tuition, command_name="myportal.tuition")

    library = sub.add_parser("library", help="Read library catalogues, course materials, account and public resources.")
    library_sub = library.add_subparsers(dest="library_cmd", required=True)

    for name in ("search", "journals"):
        command = library_sub.add_parser(name, help="Search the catalogue." if name == "search" else "Search e-journals.")
        command.add_argument("--query", required=True)
        command.add_argument("--offset", type=int, default=0)
        command.add_argument("--limit", type=int, default=20)
        if name == "search":
            command.add_argument("--scope", choices=("all", "catalogue", "books", "articles", "media", "videos"), default="all")
        command.add_argument("--json", action="store_true")
        command.set_defaults(handler=cmd_library_catalogue, command_name=f"library.{name}")

    record = library_sub.add_parser("record", help="Read a catalogue record and availability.")
    record.add_argument("--id", required=True)
    record.add_argument("--json", action="store_true")
    record.set_defaults(handler=cmd_library_catalogue, command_name="library.record")

    for name in ("status", "account", "loans", "requests", "fines"):
        command = library_sub.add_parser(name, help="Read the local Library account session.")
        if name == "status":
            command.add_argument("--service", choices=("catalogue", "papers", "syllabuses"), default="catalogue")
        command.add_argument("--json", action="store_true")
        command.set_defaults(handler=cmd_library_account, command_name=f"library.{name}")

    links = library_sub.add_parser("links", help="List the official Library website directory.")
    links.add_argument("--json", action="store_true")
    links.set_defaults(handler=cmd_library_public, command_name="library.links")

    resources = library_sub.add_parser("resources", help="List or search databases, e-books, videos and subject guides.")
    resources.add_argument("--category", choices=("all", "databases", "ebooks", "ejournals", "videos", "repositories", "guides"), default="all")
    resources.add_argument("--query", default="")
    resources.add_argument("--json", action="store_true")
    resources.set_defaults(handler=cmd_library_public, command_name="library.resources")

    for name in ("hours", "locations", "news", "services", "regulations", "membership", "remote-access", "room-booking", "laptop-loans", "forms", "staff", "alumni"):
        command = library_sub.add_parser(name, help="Read official Library information.")
        command.add_argument("--json", action="store_true")
        command.set_defaults(handler=cmd_library_public, command_name=f"library.{name}")

    for name in ("papers", "syllabuses"):
        command = library_sub.add_parser(name, help="Search examination papers." if name == "papers" else "Search course syllabuses.")
        command.add_argument("--query", default="")
        command.add_argument("--page", type=int, default=1)
        command.add_argument("--per-page", type=int, choices=(10, 20, 50, 100), default=10)
        _add_library_filters(command, paper_fields=name == "papers")
        command.add_argument("--json", action="store_true")
        command.set_defaults(handler=cmd_library_documents, command_name=f"library.{name}")

    filters = library_sub.add_parser("filters", help="List available examination paper or syllabus search filters.")
    filters.add_argument("--service", choices=("papers", "syllabuses"), required=True)
    _add_library_filters(filters)
    filters.add_argument("--json", action="store_true")
    filters.set_defaults(handler=cmd_library_documents, command_name="library.filters")

    download = library_sub.add_parser("download", help="Download one examination paper or syllabus.")
    download.add_argument("--service", choices=("papers", "syllabuses"), required=True)
    download.add_argument("--id", required=True)
    download.add_argument("--output", type=Path, required=True)
    download.add_argument("--query", default="", help="Repeat the search used to find the document.")
    download.add_argument("--json", action="store_true")
    download.set_defaults(handler=cmd_library_documents, command_name="library.download")

    library_extract = library_sub.add_parser("extract", help="Read text from a downloaded PDF, Word or PowerPoint file.")
    library_extract.add_argument("--path", type=Path, required=True)
    library_extract.add_argument("--json", action="store_true")
    library_extract.set_defaults(handler=cmd_library_extract, command_name="library.extract")
    return parser


def _add_library_filters(parser: argparse.ArgumentParser, *, paper_fields: bool = True) -> None:
    fields = [("discipline", "discipline_uuid"), ("department", "department_uuid"), ("course", "course_code"), ("year", "year")]
    if paper_fields:
        fields.extend((("session", "session"), ("level", "class")))
    for flag, field in fields:
        help_text = "Filter value from `vtc library filters`."
        if flag in {"session", "level"}:
            help_text += " Examination papers only."
        parser.add_argument(f"--{flag}", dest=f"filter_{field}", help=help_text)


def _add_site(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--site",
        required=True,
        choices=SITE_CHOICES,
        help="Moodle academic-year site. Required; there is no default.",
    )


def _wants_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _emit(payload: dict[str, Any], *, json_mode: bool) -> None:
    clean = redact_public_payload(payload)
    if json_mode:
        json.dump(clean, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    _print_human(clean)


def redact_public_payload(payload: Any) -> Any:
    blocked = {
        "password",
        "token",
        "wstoken",
        "privatetoken",
        "cookie",
        "cookies",
        "authorization",
        "otp",
        "totp",
        "secret",
        "sesskey",
        "totp_secret",
        "access_token",
        "refresh_token",
        "id_token",
        "primoexplorejwt",
        "jwt",
        "authstate",
        "samlrequest",
        "samlresponse",
        "relaystate",
        "csrf_token",
        "xsrf-token",
        "patron_id",
        "patronid",
        "useridentifier",
    }
    blocked.update(name.lower() for name in SECRET_ENV_NAMES)
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            if key.lower() in blocked:
                continue
            out[key] = redact_public_payload(value)
        return out
    if isinstance(payload, list):
        return [redact_public_payload(item) for item in payload]
    if isinstance(payload, str):
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        try:
            parsed = urlsplit(payload)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.scheme and parsed.query:
            pairs = parse_qsl(parsed.query, keep_blank_values=True)
            safe = [(key, value) for key, value in pairs if key.lower() not in blocked]
            if len(safe) != len(pairs):
                payload = urlunsplit(parsed._replace(query=urlencode(safe)))
        names = "|".join(re.escape(key) for key in blocked)
        return re.sub(rf"(?i)(\b(?:{names})\s*=\s*)[^&\s]+", r"\1redacted", payload)
    return payload


def _print_human(payload: dict[str, Any]) -> None:
    status = payload.get("status")
    site = payload.get("site") or "-"
    source = payload.get("source") or "-"
    print(f"{payload.get('command')}  site={site}  source={source}  status={status}")
    if payload.get("error"):
        print(payload["error"])
    if "courses" in payload and payload.get("command") != "myportal.timetable":
        courses = payload.get("courses") or []
        print(f"courses: {len(courses)}")
        for row in courses:
            code = row.get("code") or "-"
            print(f"  {code}  {row.get('title')}  {row.get('url')}")
    if "assignments" in payload:
        items = payload.get("assignments") or []
        print(f"assignments: {len(items)}")
        for row in items:
            due = row.get("due") or "unverified"
            print(f"  {row.get('code') or '-'}  {row.get('title')}  due={due}  {row.get('url')}")
    if payload.get("command") == "moodle.sync":
        print(f"dry_run={payload.get('dry_run')} output={payload.get('output')}")
        print(f"planned={len(payload.get('planned') or [])} downloaded={len(payload.get('downloaded') or [])}")
    if payload.get("command") == "login.moodle":
        print(f"authenticated={payload.get('authenticated')} rest={payload.get('rest')} session={payload.get('session')}")
    if payload.get("command") == "login.myportal":
        print(f"authenticated={payload.get('authenticated')} session={payload.get('session')}")
    if payload.get("command") == "moodle.status":
        print(
            f"session={payload.get('session')} rest={payload.get('rest')} authenticated={payload.get('authenticated')}"
        )
    if payload.get("command") == "myportal.status":
        print(f"session={payload.get('session')} authenticated={payload.get('authenticated')}")
    if payload.get("command") == "myportal.timetable":
        courses = payload.get("courses") or []
        print(f"week={((payload.get('week') or {}).get('label')) or '-'} courses={len(courses)}")
        for row in courses:
            print(
                f"  {row.get('weekday')}  {row.get('time_range')}  {row.get('course')}  {row.get('room')}"
            )
    if payload.get("command") == "myportal.activities":
        items = payload.get("activities") or []
        print(f"activities: {len(items)}")
        for row in items:
            print(f"  {row.get('id')}  {row.get('title')}  {row.get('status') or '-'}")
    if payload.get("command") == "myportal.modules":
        items = payload.get("modules") or []
        print(f"modules: {len(items)}")
        for row in items:
            print(f"  {row.get('code') or row.get('id')}  {row.get('title')}  {row.get('status') or '-'}")
    if payload.get("command") in {"myportal.transcript", "myportal.tuition"}:
        items = payload.get("documents") or []
        print(f"documents: {len(items)}")
        for row in items:
            print(f"  {row.get('title')}  {row.get('path') or row.get('url') or '-'}")
    if payload.get("command") == "moodle.extract":
        print(f"extract_status={payload.get('extract_status')} extractor={payload.get('extractor')}")
        if payload.get("excerpt"):
            print(payload["excerpt"])
        elif payload.get("error"):
            print(payload["error"])
    if str(payload.get("command", "")).startswith(("library.", "login.library")):
        if "authenticated" in payload:
            print(f"service={payload.get('service') or 'catalogue'} authenticated={payload.get('authenticated')}")
        for key in ("items", "documents", "resources", "links"):
            rows = payload.get(key)
            if not isinstance(rows, list):
                continue
            print(f"{key}: {len(rows)} total={payload.get('total', payload.get('result_count', '-'))}")
            for row in rows:
                title = row.get("title") or row.get("subject_title") or row.get("course_title") or row.get("type") or "-"
                year = row.get("academic_year") or row.get("issuing_year") or ""
                print(f"  {row.get('record_id') or row.get('id') or '-'}  {title}  {year}  {row.get('path') or row.get('url') or ''}")
            break
        if payload.get("command") == "library.download" and payload.get("path"):
            print(f"{payload['path']} ({payload.get('bytes', 0)} bytes)")
        if payload.get("command") == "library.extract" and payload.get("excerpt"):
            print(payload["excerpt"])
        elif payload.get("text"):
            print(payload["text"])
        if payload.get("filters"):
            print(json.dumps(payload["filters"], ensure_ascii=False, indent=2))


def _library_payload(command: str, result: dict[str, Any], **extra: Any) -> dict[str, Any]:
    payload = dict(result)
    verified = bool(payload.pop("verified", False))
    source = payload.pop("source", "unverified")
    data = payload.pop("data", {})
    if isinstance(data, dict):
        payload.update(data)
    payload.update(extra)
    return envelope(command=command, site=None, source=source if verified else "unverified", ok=verified, status="ok" if verified else "unverified", **payload)


def cmd_login_library(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.library.login import login_library

    result = login_library(service=args.service, credentials_file=args.credentials_file, headed=args.headed)
    return envelope(command="login.library", site=None, source="library_html", ok=result.authenticated, status="authenticated" if result.authenticated else "auth_failed", authenticated=result.authenticated, service=result.service, session=result.session, details=result.details)


def cmd_library_catalogue(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.errors import UsageError
    from vtc.library.catalogue import LibraryCatalogueClient

    if args.library_cmd != "record" and (not args.query.strip() or args.offset < 0 or not 1 <= args.limit <= 100):
        raise UsageError("Library search requires a query, non-negative offset and limit between 1 and 100.")
    with LibraryCatalogueClient() as client:
        if args.library_cmd == "record":
            result = client.record(args.id)
        elif args.library_cmd == "journals":
            result = client.journals(args.query, offset=args.offset, limit=args.limit)
        else:
            result = client.search(args.query, scope=args.scope, offset=args.offset, limit=args.limit)
        return _library_payload(args.command_name, result)


def cmd_library_account(args: argparse.Namespace) -> dict[str, Any]:
    if args.library_cmd == "status" and args.service != "catalogue":
        from vtc.library.documents import LibraryDocumentsClient

        with LibraryDocumentsClient(args.service) as client:
            return _library_payload(args.command_name, client.status(), service=args.service)
    from vtc.library.catalogue import LibraryCatalogueClient

    kind = "overview" if args.library_cmd in {"status", "account"} else args.library_cmd
    with LibraryCatalogueClient() as client:
        result = client.account(kind)
        return _library_payload(args.command_name, result, service="catalogue", authenticated=bool(result.get("verified")))


def cmd_library_public(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.library.public import LibraryPublicClient

    with LibraryPublicClient() as client:
        if args.library_cmd == "links":
            result = client.links()
        elif args.library_cmd == "resources":
            result = client.resources(args.category, query=args.query)
        else:
            result = client.info(args.library_cmd)
        return _library_payload(args.command_name, result)


def cmd_library_documents(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.errors import UsageError
    from vtc.library.documents import LibraryDocumentsClient

    service = args.library_cmd if args.library_cmd in {"papers", "syllabuses"} else args.service
    if service == "syllabuses" and any(getattr(args, f"filter_{field}", None) is not None for field in ("session", "class")):
        raise UsageError("Session and level filters apply to examination papers only.")
    with LibraryDocumentsClient(service) as client:
        if args.library_cmd == "filters":
            selected = {field: getattr(args, f"filter_{field}", None) for field in ("discipline_uuid", "department_uuid", "course_code", "year", "session", "class")}
            result = client.filters(filters={field: value for field, value in selected.items() if value is not None})
        elif args.library_cmd == "download":
            result = client.download(args.id, args.output, query=args.query)
        else:
            if args.page < 1:
                raise UsageError("Library page must be at least 1.")
            selected = {field: getattr(args, f"filter_{field}", None) for field in ("discipline_uuid", "department_uuid", "course_code", "year", "session", "class")}
            result = client.search(args.query, page=args.page, per_page=args.per_page, filters={field: value for field, value in selected.items() if value is not None})
        return _library_payload(args.command_name, result, service=service)


def cmd_library_extract(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.moodle.extract import extract_file

    extracted = extract_file(args.path)
    ok = extracted.status == "ok"
    return envelope(command="library.extract", site=None, source="library_html" if ok else "unverified", ok=ok, status=extracted.status, path=str(args.path), extractor=extracted.extractor, extract_status=extracted.status, excerpt=extracted.excerpt, key_lines=extracted.key_lines, text=extracted.text, error=extracted.error)


def cmd_login_moodle(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.login_moodle import login_moodle

    result = login_moodle(
        args.site,
        store_password=args.store_password,
        store_totp=args.store_totp,
        totp_auto=args.totp_auto,
        headed=args.headed,
        credentials_file=args.credentials_file,
    )
    return envelope(
        command="login.moodle",
        site=result.site,
        source="moodle_html",
        ok=result.authenticated,
        status="authenticated" if result.authenticated else "auth_failed",
        authenticated=result.authenticated,
        rest=result.rest,
        session=result.session,
        details=result.details,
    )


def cmd_login_myportal(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.myportal.login import login_myportal

    result = login_myportal(
        store_password=args.store_password,
        headed=args.headed,
        credentials_file=args.credentials_file,
    )
    return envelope(
        command="login.myportal",
        site=None,
        source="myportal_html",
        ok=result.authenticated,
        status="authenticated" if result.authenticated else "auth_failed",
        authenticated=result.authenticated,
        session=result.session,
        details=result.details,
    )


def _require_human_write(action: str, *, confirm: bool) -> None:
    if not confirm or not stdin_is_tty():
        raise WriteBlocked(
            f"{action} changes a MyPortal record. Run it in a local terminal with --confirm. "
            "Agents must not register activities or select modules."
        )


def _myportal_unverified(command: str, error: str) -> dict[str, Any]:
    return envelope(
        command=command,
        site=None,
        source="unverified",
        ok=False,
        status="unverified",
        session=myportal_session_path().exists(),
        authenticated=False,
        error=error,
    )


def cmd_myportal_status(args: argparse.Namespace) -> dict[str, Any]:
    path = myportal_session_path()
    if not path.exists():
        return _myportal_unverified(
            "myportal.status",
            "No MyPortal session. Run `vtc login myportal` in a local terminal.",
        )
    from vtc.myportal.session import MyPortalSession

    with MyPortalSession() as client:
        result = client.status()
        return envelope(
            command="myportal.status",
            site=None,
            source="myportal_html",
            ok=bool(result.get("authenticated")),
            status="ok" if result.get("authenticated") else "unverified",
            session=True,
            authenticated=bool(result.get("authenticated")),
            nav=result.get("nav") or [],
        )


def cmd_myportal_timetable(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.myportal.session import MyPortalSession

    with MyPortalSession() as client:
        result = client.timetable(today_only=args.today)
        found = bool(result.get("found"))
        return envelope(
            command="myportal.timetable",
            site=None,
            source="myportal_html" if found else "unverified",
            ok=found,
            status="ok" if found else "unverified",
            today=args.today,
            week=result.get("week"),
            weekdays=result.get("weekdays") or [],
            courses=result.get("courses") or [],
            today_weekday=result.get("today_weekday"),
            today_courses=result.get("today_courses") or [],
            remarks=result.get("remarks") or [],
            note=None
            if found
            else (result.get("details") or "Timetable was not read. That is unverified, not an empty week."),
        )


def cmd_myportal_activities(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.myportal.session import MyPortalSession

    with MyPortalSession() as client:
        result = client.activities()
        items = result.get("activities") or []
        found = bool(result.get("found"))
        return envelope(
            command="myportal.activities",
            site=None,
            source="myportal_html" if found else "unverified",
            ok=found and bool(items),
            status="ok" if found and items else "unverified",
            activities=items,
            note=None
            if found and items
            else "No activity records were read. That is unverified, not proof that there are no activities.",
        )


def cmd_myportal_modules(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.myportal.session import MyPortalSession

    with MyPortalSession() as client:
        result = client.modules()
        items = result.get("modules") or []
        found = bool(result.get("found"))
        return envelope(
            command="myportal.modules",
            site=None,
            source="myportal_html" if found else "unverified",
            ok=found and bool(items),
            status="ok" if found and items else "unverified",
            modules=items,
            note=None
            if found and items
            else "No module-selection records were read. That is unverified, not proof that selection is closed.",
        )


def cmd_myportal_apply(args: argparse.Namespace) -> dict[str, Any]:
    _require_human_write("Activity registration", confirm=args.confirm)
    from vtc.myportal.session import MyPortalSession

    with MyPortalSession() as client:
        result = client.apply_activity(args.id)
        applied = bool(result.get("applied"))
        return envelope(
            command="myportal.apply",
            site=None,
            source="myportal_html" if applied else "unverified",
            ok=applied,
            status="ok" if applied else "unverified",
            applied=applied,
            action_clicked=bool(result.get("action_clicked")),
            matched=result.get("matched"),
            activities=result.get("activities") or [],
            note=result.get("details"),
        )


def cmd_myportal_select(args: argparse.Namespace) -> dict[str, Any]:
    _require_human_write("Module selection", confirm=args.confirm)
    from vtc.myportal.session import MyPortalSession

    with MyPortalSession() as client:
        result = client.select_module(args.code)
        selected = bool(result.get("selected"))
        return envelope(
            command="myportal.select",
            site=None,
            source="myportal_html" if selected else "unverified",
            ok=selected,
            status="ok" if selected else "unverified",
            selected=selected,
            action_clicked=bool(result.get("action_clicked")),
            matched=result.get("matched"),
            modules=result.get("modules") or [],
            note=result.get("details"),
        )


def _cmd_myportal_documents(command: str, kind: str, output: Path | None) -> dict[str, Any]:
    from vtc.myportal.session import MyPortalSession

    with MyPortalSession() as client:
        result = client.download_documents(kind, output) if output else client.documents(kind)
        items = result.get("documents") or []
        found = bool(result.get("found"))
        complete = found and bool(items) and (not output or all(item.get("path") for item in items))
        return envelope(
            command=command,
            site=None,
            source="myportal_html" if found else "unverified",
            ok=complete,
            status="ok" if complete else "unverified",
            kind=kind,
            output=str(output) if output else None,
            documents=items,
            note=None
            if complete
            else (
                f"{kind.capitalize()} files were not fully read or downloaded. That is unverified, not proof that the document is missing. "
                "Document Download files expire after a short window."
            ),
        )


def cmd_myportal_transcript(args: argparse.Namespace) -> dict[str, Any]:
    return _cmd_myportal_documents("myportal.transcript", "transcript", args.output)


def cmd_myportal_tuition(args: argparse.Namespace) -> dict[str, Any]:
    return _cmd_myportal_documents("myportal.tuition", "tuition", args.output)


def cmd_moodle_status(args: argparse.Namespace) -> dict[str, Any]:
    path = session_path(args.site)
    rest_path = rest_status_path(args.site)
    rest = "unavailable"
    if rest_path.exists():
        try:
            rest = json.loads(rest_path.read_text(encoding="utf-8")).get("rest") or "unavailable"
        except Exception:
            rest = "unavailable"
    if not path.exists():
        return envelope(
            command="moodle.status",
            site=args.site,
            source="unverified",
            ok=False,
            status="unverified",
            session=False,
            rest=rest,
            authenticated=False,
            error=f"No Moodle session for {args.site}. Run `vtc login moodle --site {args.site}` in a local terminal.",
        )
    try:
        from vtc.moodle.client import MoodleClient

        with MoodleClient(args.site) as client:
            source, courses = client.courses()
            rest = client.rest_status()
            return envelope(
                command="moodle.status",
                site=args.site,
                source=source,
                ok=True,
                status="ok",
                session=True,
                rest=rest,
                authenticated=True,
                course_count=len(courses),
            )
    except MissingSession as exc:
        return envelope(
            command="moodle.status",
            site=args.site,
            source="unverified",
            ok=False,
            status="unverified",
            session=path.exists(),
            rest=rest,
            authenticated=False,
            error=str(exc),
        )


def cmd_moodle_courses(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.moodle.client import MoodleClient

    with MoodleClient(args.site) as client:
        source, courses = client.courses()
        return envelope(
            command="moodle.courses",
            site=args.site,
            source=source,
            ok=True,
            status="ok",
            courses=courses,
        )


def cmd_moodle_assignments(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.moodle.client import MoodleClient

    with MoodleClient(args.site) as client:
        source, items = client.assignments(args.course)
        status = "ok" if items else "unverified"
        return envelope(
            command="moodle.assignments",
            site=args.site,
            source=source if items else "unverified",
            ok=bool(items),
            status=status,
            course=args.course,
            assignments=items,
            note=None
            if items
            else "No assignment records were read. That is unverified, not proof that there are no assignments.",
        )


def cmd_moodle_sync(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.moodle.client import MoodleClient
    from vtc.moodle.sync import sync_course

    with MoodleClient(args.site) as client:
        result = sync_course(
            client,
            args.course,
            args.output,
            dry_run=args.dry_run,
            extract=not args.no_extract,
        )
        source = result.pop("source", "unverified")
        return envelope(
            command="moodle.sync",
            site=args.site,
            source=source,
            ok=True,
            status="ok",
            **result,
        )


def cmd_moodle_extract(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.moodle.extract import extract_file

    extracted = extract_file(args.path)
    ok = extracted.status == "ok"
    return envelope(
        command="moodle.extract",
        site=None,
        source="moodle_html" if ok else "unverified",
        ok=ok,
        status=extracted.status,
        path=str(args.path),
        extractor=extracted.extractor,
        extract_status=extracted.status,
        excerpt=extracted.excerpt,
        key_lines=extracted.key_lines,
        text=extracted.text,
        error=extracted.error,
    )


if __name__ == "__main__":
    raise SystemExit(main())
