from __future__ import annotations

from dataclasses import dataclass

SITE_CHOICES = ("ay2526", "ay2627")

_SITES = {
    "ay2526": "https://moodle2526.vtc.edu.hk",
    "ay2627": "https://moodle2627.vtc.edu.hk",
}

STUDENT_IDP = "23a1b87709dc35d458d4ad045a53ad28"
STUDENT_EMAIL_DOMAIN = "stu.vtc.edu.hk"


@dataclass(frozen=True)
class MoodleSite:
    key: str
    base_url: str

    @property
    def host(self) -> str:
        return self.base_url.removeprefix("https://").removeprefix("http://")

    def url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    def student_email(self, student_id: str) -> str:
        if "@" in student_id:
            return student_id
        return f"{student_id}@{STUDENT_EMAIL_DOMAIN}"

    def saml_login_url(self) -> str:
        wants = self.url("/login/index.php")
        from urllib.parse import quote

        return (
            f"{self.base_url}/auth/saml2rpa/login.php"
            f"?wants={quote(wants, safe='')}"
            f"&idp={STUDENT_IDP}&passive=off"
        )


def parse_site(value: str) -> MoodleSite:
    key = (value or "").strip().lower()
    if key not in _SITES:
        from vtc.errors import UsageError

        raise UsageError(
            "Moodle --site is required and must be ay2526 or ay2627; there is no default."
        )
    return MoodleSite(key=key, base_url=_SITES[key])
