from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from vtc import __version__
from vtc.errors import MissingSession, VtcError, WriteBlocked
from vtc.paths import myportal_session_path, rest_status_path, session_path
from vtc.secrets import stdin_is_tty
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
        description="Read-only VTC Moodle and MyPortal CLI. Login must run in a local terminal.",
    )
    parser.add_argument("--version", action="version", version=f"vtc {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    login = sub.add_parser("login", help="Store a local session. Run this in a terminal, not chat.")
    login_sub = login.add_subparsers(dest="target", required=True)

    moodle_login = login_sub.add_parser("moodle", help="Sign in to VTC Moodle via Student SSO.")
    _add_site(moodle_login)
    moodle_login.add_argument("--json", action="store_true")
    moodle_login.add_argument("--headed", action="store_true", help="Show the browser window.")
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
    myportal_login.add_argument(
        "--store-password",
        action="store_true",
        help="Save the password to macOS Keychain after it is read.",
    )
    myportal_login.set_defaults(handler=cmd_login_myportal, command_name="login.myportal")

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
    return parser


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
    }
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            if key.lower() in blocked:
                continue
            out[key] = redact_public_payload(value)
        return out
    if isinstance(payload, list):
        return [redact_public_payload(item) for item in payload]
    if isinstance(payload, str) and "wstoken=" in payload.lower():
        return payload.split("wstoken=", 1)[0] + "wstoken=redacted"
    if isinstance(payload, str) and "token=" in payload and "pluginfile.php" in payload:
        from urllib.parse import urlparse

        parsed = urlparse(payload)
        return parsed._replace(query="").geturl()
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


def cmd_login_moodle(args: argparse.Namespace) -> dict[str, Any]:
    from vtc.login_moodle import login_moodle

    result = login_moodle(
        args.site,
        store_password=args.store_password,
        store_totp=args.store_totp,
        totp_auto=args.totp_auto,
        headed=args.headed,
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

    result = login_myportal(store_password=args.store_password, headed=args.headed)
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
            ok=True,
            status="ok" if items else "unverified",
            activities=items,
            note=None
            if items
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
            ok=True,
            status="ok" if items else "unverified",
            modules=items,
            note=None
            if items
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
        return envelope(
            command=command,
            site=None,
            source="myportal_html" if found else "unverified",
            ok=True,
            status="ok" if items else "unverified",
            kind=kind,
            output=str(output) if output else None,
            documents=items,
            note=None
            if items
            else (
                f"No {kind} files were read. That is unverified, not proof that the document is missing. "
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
            ok=True,
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
        return envelope(
            command="moodle.sync",
            site=args.site,
            source=result.get("source") or "unverified",
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
