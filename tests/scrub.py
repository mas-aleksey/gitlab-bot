"""Anonymise VCR cassettes so recordings from a private GitLab can ship publicly.

`filter_headers` in the VCR config only strips the PAT header — response *bodies*
are stored verbatim, gzipped and base64-encoded, so a plain grep over the cassette
files finds nothing and the leak looks like it isn't there. It is: usernames,
emails, real names, project paths and the instance host all live in those bodies.

Scrubbing works by *value*, not by field name. An allowlist of fields ("username",
"web_url", …) misses whatever GitLab adds next — `_links.self`, `readme_url` and
`description_html` all carried the namespace when this was written — and misses
URL-encoded copies (`group%2Fproject`) entirely. So the run has two passes:

1. **collect** — walk every response body and learn which real identifiers exist
   (host, namespaces, people, branches), keyed by the fields that reliably name
   them;
2. **replace** — substitute every occurrence of those learned strings across the
   whole cassette text, bodies and request URIs alike. Percent-encoded paths are
   decoded first so that `%2F`-chained segments are ordinary tokens.

Every mapping is deterministic and collision-free: one real value always becomes
the same fake one, so relations survive (a commit's author stays one person across
responses) and tests comparing two fields still pass.

    python -m tests.scrub                 # rewrite in place
    python -m tests.scrub --check         # exit 1 if anything still looks private

conftest.py wires pass 1+2 into `before_record_response`, so a freshly recorded
cassette is scrubbed before it ever reaches disk.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

CASSETTE_DIR = Path(__file__).parent / "clients" / "gitlab" / "cassettes"

FAKE_HOST = "gl.example.com"
FAKE_ROOT_GROUP = "acme-dev"
# The two paths the fixtures feed into request URIs. VCR matches on URL, so these
# must be exactly what conftest.py's scope_path / test_project_path defaults return —
# scrub the cassettes to different values and every recorded request stops matching.
FAKE_SCOPE_PATH = "acme-dev/platform"
FAKE_PROJECT_PATH = "acme-dev/platform/backend"
FAKE_EMAIL_DOMAIN = "example.com"

_HOST_RE = re.compile(r"uri: https?://([^/\s]+)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
_GRAVATAR_RE = re.compile(r"https?://(?:secure\.|www\.)?gravatar\.com/[^\s\"'\\)]*")
# Uploads appear both absolute and host-relative ("/uploads/-/system/group/
# avatar/327/mygroup-logo.png"), and the file name carries the group slug.
_UPLOAD_RE = re.compile(r"(?:https?://[\w.-]+)?/uploads/[^\s\"'\\)]*")
# Stand-in for %2F while substitutions run: a word boundary for the patterns below,
# and absent from any GitLab payload.
_SEP = "\x00"
# Links out to whatever the instance integrates with (issue trackers, wikis, CI
# dashboards). Everything on the fake host is ours and stays.
_EXTERNAL_URL_RE = re.compile(
    rf"https?://(?!{re.escape(FAKE_HOST)})[\w.-]+\.[a-z]{{2,}}(?:/[^\s\"'\\)]*)?"
)

# Fields that reliably *name* an identifier, used only to learn the mapping in
# pass 1. Replacement itself is global, so this list need not be exhaustive.
_PERSON_FIELDS = ("username", "name", "author_name", "committer_name")
_EMAIL_FIELDS = (
    "email",
    "public_email",
    "commit_email",
    "author_email",
    "committer_email",
)
_PATH_FIELDS = ("path_with_namespace", "full_path", "path", "group_full_path")
# Human-readable namespace labels: "Acme Dev / Platform / Backend". They name
# the same private groups as the path fields, in a different alphabet.
_DISPLAY_PATH_FIELDS = ("name_with_namespace", "full_name", "group_name")
_REF_FIELDS = ("ref", "source_branch", "target_branch", "default_branch")
# A branch object carries its name in plain "name", same key a user or group uses.
# These sibling keys are what tells the three apart.
_BRANCH_MARKERS = frozenset({"can_push", "protected", "merged", "developers_can_push"})
# Free text carries ticket ids and internal wording; no test asserts on content.
_TEXT_FIELDS = ("title", "description", "description_html", "message", "commit_title")

_KEEP_BRANCHES = frozenset(
    {"main", "master", "dev", "develop", "staging", "production", "HEAD"}
)
# Display names that are pure GitLab plumbing, not people.
_KEEP_NAMES = frozenset({"gitlab-ci", "GitLab", "Administrator", "Ghost User"})
# Values this script itself produces. Recognising them keeps a second run a no-op:
# without this the scrubber re-learns its own aliases and renames them again, and
# the cassette URIs drift away from what the fixtures request.
_ALREADY_FAKE = re.compile(
    rf"^(?:user\d+|feature/branch\d+|{re.escape(FAKE_ROOT_GROUP)}(?:-seg\d+)?"
    rf"|platform|backend|redacted|avatar\.png)$"
)


class Anonymiser:
    """Stable real -> fake mapping, shared across every cassette in one run."""

    def __init__(self, scope_path: str | None = None, project_path: str | None = None) -> None:
        self.host: str | None = None
        self.people: dict[str, str] = {}
        self.paths: dict[str, str] = {}
        self.branches: dict[str, str] = {}
        # Pin the two paths the fixtures also produce, so cassette URIs keep matching
        # the requests the suite makes. Everything else gets a generated stand-in.
        self._pin_path(scope_path, FAKE_SCOPE_PATH)
        self._pin_path(project_path, FAKE_PROJECT_PATH)

    def _pin_path(self, real: str | None, fake: str) -> None:
        """Map a known namespace segment-by-segment onto a chosen fake one."""
        if not real:
            return
        real_parts, fake_parts = real.split("/"), fake.split("/")
        if len(real_parts) != len(fake_parts):
            raise ValueError(f"{real!r} and {fake!r} must have the same depth")
        for real_part, fake_part in zip(real_parts, fake_parts, strict=True):
            self.paths.setdefault(real_part, fake_part)

    # -- pass 1: learn ------------------------------------------------------------

    def learn_person(self, value: str) -> None:
        if _ALREADY_FAKE.match(value or ""):
            return
        if not value or value in _KEEP_NAMES or value in self.people:
            return
        self.people[value] = f"user{len(self.people) + 1}"

    def learn_email(self, value: str) -> None:
        if not value or "@" not in value:
            return
        local, _, _ = value.partition("@")
        if local != "git":
            self.learn_person(local)

    def learn_path(self, value: str) -> None:
        """Learn each path segment and the full path, longest first at replace time.

        A namespace arrives as `group/sub/project` in one field and as bare `project`
        in another, so both the whole string and its segments must map.
        """
        if not value or "@" in value or value.startswith("refs/"):
            return
        for part in value.split("/"):
            if part and part not in self.paths and not _ALREADY_FAKE.match(part):
                self.paths[part] = f"{FAKE_ROOT_GROUP}-seg{len(self.paths) + 1}"

    def learn_display_path(self, value: str) -> None:
        """Learn each segment of a `Group / Subgroup / Project` label."""
        for part in value.split("/"):
            part = part.strip()
            if part and part not in _KEEP_NAMES:
                self.learn_person(part)

    def learn_branch(self, value: str) -> None:
        if _ALREADY_FAKE.match(value or ""):
            return
        if not value or value in _KEEP_BRANCHES or value.startswith("refs/"):
            return
        if value not in self.branches:
            self.branches[value] = f"feature/branch{len(self.branches) + 1}"

    # Free text is redacted structurally in `redact_text_fields`, not learned:
    # GitLab returns overlapping variants of the same prose across endpoints
    # (description vs. description_html vs. a commit message quoting it), and
    # exact-string mapping only catches whichever variant it saw first.

    def collect(self, node: Any) -> None:
        if isinstance(node, dict):
            is_branch = bool(_BRANCH_MARKERS & node.keys())
            for key, val in node.items():
                if isinstance(val, str):
                    if key == "name" and is_branch:
                        self.learn_branch(val)
                    elif key in _PERSON_FIELDS:
                        self.learn_person(val)
                    elif key in _EMAIL_FIELDS:
                        self.learn_email(val)
                    elif key in _PATH_FIELDS:
                        self.learn_path(val)
                    elif key in _DISPLAY_PATH_FIELDS:
                        self.learn_display_path(val)
                    elif key in _REF_FIELDS:
                        self.learn_branch(val)
                self.collect(val)
        elif isinstance(node, list):
            for item in node:
                self.collect(item)

    # -- pass 2: replace ----------------------------------------------------------

    def redact_text_fields(self, node: Any) -> Any:
        """Blank out free-text fields in place, before any string substitution.

        Ticket ids, internal wording and links to an external tracker live here and
        nothing in the suite asserts on their content — only that they are strings.
        """
        if isinstance(node, dict):
            return {
                key: ("redacted" if key in _TEXT_FIELDS and isinstance(val, str) and val
                      else self.redact_text_fields(val))
                for key, val in node.items()
            }
        if isinstance(node, list):
            return [self.redact_text_fields(item) for item in node]
        return node

    def _pairs(self) -> list[tuple[str, str]]:
        """Every mapping, longest real value first.

        Order matters: replacing `sub` before `group/sub/project` would corrupt
        the longer string into fragments that no longer match anything.
        """
        pairs: list[tuple[str, str]] = []
        pairs.extend(self.branches.items())
        pairs.extend(self.paths.items())
        pairs.extend(self.people.items())
        if self.host:
            pairs.append((self.host, FAKE_HOST))
        return sorted(pairs, key=lambda kv: len(kv[0]), reverse=True)

    def apply(self, text: str) -> str:
        """Replace every learned identifier across a chunk of cassette text.

        Word boundaries keep short segments from eating unrelated substrings: the
        namespace segment `rp` must not rewrite the `rp` inside `corporate`.
        """
        # Structural URLs go first: once the substitution loop has rewritten a
        # segment inside them, these patterns no longer match what they target.
        text = _GRAVATAR_RE.sub(f"https://{FAKE_HOST}/avatar.png", text)
        text = _UPLOAD_RE.sub("/uploads/avatar.png", text)
        # `%2F` chains several path segments into one token, so a plain substitution
        # only ever rewrites the first. Decode to a sentinel the patterns treat as a
        # boundary, substitute, then restore the encoding — GitLab routes
        # /groups/a%2Fb and /groups/a/b to different endpoints, and VCR matches on the
        # URL, so losing the encoding silently breaks replay.
        text = re.sub(r"%2F", _SEP, text, flags=re.IGNORECASE)
        for real, fake in self._pairs():
            if not real:
                continue
            text = re.sub(rf"(?<![\w.-]){re.escape(real)}(?![\w-])", fake, text)
        text = text.replace(_SEP, "%2F")
        # Any address that survived belongs to a domain we never learned.
        text = _EMAIL_RE.sub(self._mask_email, text)
        # Third-party hosts the instance links out to. A gravatar path is a hash of
        # the user's email — an identifier, not decoration — and an issue tracker URL
        # names internal tickets, so both get a neutral stand-in.
        text = _EXTERNAL_URL_RE.sub(f"https://{FAKE_HOST}/link", text)
        return text

    def _mask_email(self, match: re.Match[str]) -> str:
        value = match.group(0)
        local, _, domain = value.partition("@")
        if domain in (FAKE_EMAIL_DOMAIN, FAKE_HOST):
            return value
        if local == "git":
            return f"git@{FAKE_HOST}"
        self.learn_person(local)
        # No mapping means learn_person recognised an alias this script already
        # produced; keeping the local part verbatim is what makes a re-run a no-op.
        return f"{self.people.get(local, local)}@{FAKE_EMAIL_DOMAIN}"


# -- cassette I/O ----------------------------------------------------------------

_BODY_RE = re.compile(r"(string: !!binary \|\n)((?:[ \t]+[A-Za-z0-9+/=]+\n)+)")


def _decode(b64: str) -> Any | None:
    try:
        return json.loads(gzip.decompress(base64.b64decode(b64)))
    except Exception:
        return None


def _encode(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    # mtime=0 keeps the gzip header byte-stable, so an unchanged cassette does not
    # show up as modified in git on every run.
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(payload)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _wrap(b64: str, indent: str) -> str:
    return "\n".join(indent + b64[i : i + 76] for i in range(0, len(b64), 76)) + "\n"


def scrub_text(text: str, anon: Anonymiser) -> str:
    """Scrub one cassette's YAML, bodies included, using the shared mapping."""
    if anon.host is None:
        hosts = {m.group(1) for m in _HOST_RE.finditer(text)} - {FAKE_HOST}
        anon.host = next(iter(hosts), None)

    bodies = [(m, _decode("".join(m.group(2).split()))) for m in _BODY_RE.finditer(text)]
    for _, data in bodies:
        if data is not None:
            anon.collect(data)

    for match, data in reversed(bodies):
        if data is None:
            continue
        # _BODY_RE only matches indented base64 lines, so the leading run always exists.
        indent_match = re.match(r"[ \t]+", match.group(2))
        indent = indent_match.group(0) if indent_match else "        "
        redacted = anon.redact_text_fields(data)
        scrubbed = json.loads(anon.apply(json.dumps(redacted, ensure_ascii=False)))
        text = text[: match.start(2)] + _wrap(_encode(scrubbed), indent) + text[match.end(2) :]

    # Request URIs, Link headers and anything else outside a body. Base64 payloads
    # are already scrubbed and must not be touched again — a substitution inside
    # one would corrupt it — so replace only the gaps between them.
    out: list[str] = []
    cursor = 0
    for match in _BODY_RE.finditer(text):
        out.append(anon.apply(text[cursor : match.start(2)]))
        out.append(match.group(2))
        cursor = match.end(2)
    out.append(anon.apply(text[cursor:]))
    return "".join(out)


def scrub_file(path: Path, anon: Anonymiser) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = scrub_text(original, anon)
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def check_text(text: str, known_host: str | None = None) -> list[str]:
    """Report anything that still looks private, bodies decoded."""
    haystack = text
    for match in _BODY_RE.finditer(text):
        with contextlib.suppress(Exception):
            haystack += gzip.decompress(
                base64.b64decode("".join(match.group(2).split()))
            ).decode("utf-8", "replace")

    findings = []
    for email in sorted(set(_EMAIL_RE.findall(haystack))):
        _, _, domain = email.partition("@")
        if domain not in (FAKE_EMAIL_DOMAIN, FAKE_HOST):
            findings.append(f"email: {email}")
    for host in sorted(set(_HOST_RE.findall(haystack)) - {FAKE_HOST}):
        findings.append(f"host in uri: {host}")
    # Any hostname-looking token outside the fake domain: catches `gl.corp.local`
    # hiding in web_url or _links, which the uri check alone would miss.
    for host in sorted(set(re.findall(r"https?://([\w.-]+)", haystack)) - {FAKE_HOST}):
        findings.append(f"host in body: {host}")
    return findings


# -- recording hook ---------------------------------------------------------------

# One anonymiser for the whole recording session, so a person keeps the same alias
# across cassettes exactly as in a file-based run.
_RECORDING_ANON: Anonymiser | None = None


def _recording_anonymiser() -> Anonymiser:
    global _RECORDING_ANON
    if _RECORDING_ANON is None:
        real_host = os.getenv("GITLAB_URL", "")
        _RECORDING_ANON = Anonymiser(
            scope_path=os.getenv("GITLAB_SCOPE_PATH"),
            project_path=os.getenv("GITLAB_TEST_PROJECT_PATH"),
        )
        _RECORDING_ANON.host = real_host.split("://")[-1].strip("/") or None
    return _RECORDING_ANON


def scrub_response(response: dict[str, Any]) -> dict[str, Any]:
    """VCR `before_record_response` hook: anonymise a body before it hits disk.

    Bodies arrive either as raw bytes or already gzipped, depending on the response's
    content-encoding; anything that is not JSON is passed through untouched, since
    the point is to scrub identifiers, not to reshape the recording.
    """
    body = response.get("body", {}).get("string")
    if not body:
        return response

    raw = body.encode("utf-8") if isinstance(body, str) else body
    gzipped = raw[:2] == b"\x1f\x8b"
    try:
        payload = gzip.decompress(raw) if gzipped else raw
        data = json.loads(payload)
    except Exception:
        return response

    anon = _recording_anonymiser()
    anon.collect(data)
    scrubbed = json.loads(anon.apply(json.dumps(anon.redact_text_fields(data), ensure_ascii=False)))
    out = json.dumps(scrubbed, ensure_ascii=False).encode("utf-8")

    if gzipped:
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(out)
        out = buf.getvalue()
    response["body"]["string"] = out

    # The URI and Link headers carry the same identifiers as the body.
    if "url" in response:
        response["url"] = anon.apply(response["url"])
    for name, values in (response.get("headers") or {}).items():
        if name.lower() in ("link", "location", "x-total-pages"):
            response["headers"][name] = [anon.apply(str(v)) for v in values]
    return response


def main(argv: list[str]) -> int:
    files = sorted(CASSETTE_DIR.rglob("*.yaml"))
    if not files:
        print(f"no cassettes under {CASSETTE_DIR}", file=sys.stderr)
        return 1

    if "--check" in argv:
        failed = False
        for path in files:
            for finding in check_text(path.read_text(encoding="utf-8")):
                print(f"{path.relative_to(CASSETTE_DIR.parent)}: {finding}")
                failed = True
        print("cassettes NOT clean" if failed else "cassettes clean")
        return 1 if failed else 0

    # .env holds the recording-time paths; without it the pins are empty and the
    # scrubbed URIs drift away from what the fixtures request.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    # The recording-time paths come from the same .env that produced the cassettes,
    # so the scrubbed URIs line up with what the fixtures will request on replay.
    anon = Anonymiser(
        scope_path=os.getenv("GITLAB_SCOPE_PATH"),
        project_path=os.getenv("GITLAB_TEST_PROJECT_PATH"),
    )
    changed = sum(scrub_file(path, anon) for path in files)
    print(f"scrubbed {changed}/{len(files)} cassettes")
    print(f"  host:       {anon.host} -> {FAKE_HOST}")
    print(f"  people:     {len(anon.people)}")
    print(f"  paths:      {len(anon.paths)}")
    print(f"  branches:   {len(anon.branches)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
