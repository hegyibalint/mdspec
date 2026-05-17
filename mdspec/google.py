"""Google Drive integration: fetch a Doc as Markdown plus its comment threads."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

_DOC_ID_PATTERNS = [
    re.compile(r"/document/d/([a-zA-Z0-9_-]+)"),
    re.compile(r"/d/([a-zA-Z0-9_-]+)"),
    re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),
]


def parse_doc_id(spec: str) -> str:
    if not spec:
        raise ValueError("doc id or URL required")
    if "/" not in spec and "?" not in spec:
        return spec
    for pattern in _DOC_ID_PATTERNS:
        match = pattern.search(spec)
        if match:
            return match.group(1)
    raise ValueError(f"could not extract a Google Doc id from {spec!r}")


def config_path(override: Optional[Path] = None) -> Path:
    if override:
        return override.expanduser()
    env = os.environ.get("MDSPEC_CONFIG")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "mdspec" / "config.toml"


def token_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "mdspec" / "google-token.json"


def _google_client_config(config_file: Path) -> dict:
    import tomllib

    if not config_file.exists():
        sys.exit(
            f"error: mdspec config not found at {config_file}\n"
            f"create it with:\n\n"
            f"  [google]\n"
            f"  client_id = \"<your-oauth-client-id>\"\n"
            f"  client_secret = \"<your-oauth-client-secret>\"\n"
        )
    with config_file.open("rb") as fh:
        config = tomllib.load(fh)
    google = config.get("google") or {}
    client_id = google.get("client_id")
    client_secret = google.get("client_secret")
    if not client_id or not client_secret:
        sys.exit(f"error: [google] client_id / client_secret missing in {config_file}")
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }


def run_consent(config_file: Path, force: bool = False):
    """Load (or obtain) Google credentials, refreshing or running consent as needed."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    tok_path = token_path()
    creds: Optional[Credentials] = None

    if not force and tok_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(tok_path), SCOPES)
        except Exception as exc:
            print(f"warning: failed to load cached token ({exc}); re-authenticating", file=sys.stderr)
            creds = None

    if not force and creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            print(f"warning: token refresh failed ({exc}); re-authenticating", file=sys.stderr)
            creds = None

    if force or not creds or not creds.valid:
        client_config = _google_client_config(config_file)
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        creds = flow.run_local_server(port=0)
        tok_path.parent.mkdir(parents=True, exist_ok=True)
        tok_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def build_services(creds):
    from googleapiclient.discovery import build

    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    return drive, docs


def export_markdown(service, doc_id: str) -> str:
    data: bytes = service.files().export(fileId=doc_id, mimeType="text/markdown").execute()
    return data.decode("utf-8")


def list_tabs(docs_service, doc_id: str) -> list[dict]:
    """Return a flat list of tabs in display order: [{id, title, depth}, ...]."""
    doc = docs_service.documents().get(documentId=doc_id, includeTabsContent=True).execute()

    def walk(tabs: list[dict], depth: int, out: list[dict]) -> None:
        for tab in tabs:
            props = tab.get("tabProperties") or {}
            tab_id = props.get("tabId")
            if tab_id:
                out.append({"id": tab_id, "title": props.get("title", ""), "depth": depth})
            walk(tab.get("childTabs") or [], depth + 1, out)

    out: list[dict] = []
    walk(doc.get("tabs") or [], 0, out)
    return out


def export_tab_markdown(creds, doc_id: str, tab_id: str) -> str:
    """Fetch a single tab's markdown via the docs.google.com export endpoint.

    The Drive API `files.export` ignores tab parameters and returns all tabs
    concatenated; the user-facing export URL accepts `?tab=<tabId>` and is the
    only way to get per-tab markdown today.
    """
    from google.auth.transport.requests import AuthorizedSession

    session = AuthorizedSession(creds)
    url = f"https://docs.google.com/document/d/{doc_id}/export?format=md&tab={tab_id}"
    response = session.get(url)
    response.raise_for_status()
    return response.text


def assemble_tab_markdown(creds, doc_id: str, tabs: list[dict]) -> str:
    parts: list[str] = []
    for tab in tabs:
        title = tab["title"].strip() or tab["id"]
        heading = "#" * min(tab["depth"] + 1, 6)
        body = export_tab_markdown(creds, doc_id, tab["id"]).lstrip()
        parts.append(f"{heading} {title}\n\n{body.rstrip()}")
    return "\n\n".join(parts) + "\n"


def fetch_comments(service, doc_id: str) -> list[dict]:
    fields = (
        "comments(id,content,quotedFileContent/value,author/displayName,"
        "createdTime,resolved,replies(content,author/displayName,createdTime))"
    )
    out: list[dict] = []
    page_token: Optional[str] = None
    while True:
        resp = service.comments().list(
            fileId=doc_id,
            fields=f"nextPageToken,{fields}",
            pageSize=100,
            pageToken=page_token,
            includeDeleted=False,
        ).execute()
        out.extend(resp.get("comments", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def insert_references(markdown: str, comments: list[dict]) -> str:
    comments = [comment for comment in comments if not comment.get("resolved", False)]
    if not comments:
        return markdown

    body = markdown
    threads: list[str] = []

    for index, comment in enumerate(comments, start=1):
        quoted = (comment.get("quotedFileContent") or {}).get("value", "")
        anchored = False
        if quoted:
            position = body.find(quoted)
            if position != -1:
                end = _shift_past_code_context(body, position, position + len(quoted))
                label = _comment_body_label(index)
                body = body[:end] + f" \\[{index}\\]{{{{mdspec-typst:#metadata(none) <{label}>}}}}" + body[end:]
                anchored = True
            else:
                print(
                    f"warning: comment [{index}] anchor not found in markdown export",
                    file=sys.stderr,
                )
        threads.append(_format_thread_md(index, comment, anchored))

    if not threads:
        return body

    return body.rstrip() + "\n\n# Comments\n\n" + "\n\n".join(threads) + "\n"


def _format_thread_md(num: int, comment: dict, anchored: bool) -> str:
    author = (comment.get("author") or {}).get("displayName", "Unknown")
    body_text = (comment.get("content") or "").strip()
    quoted = (comment.get("quotedFileContent") or {}).get("value", "").strip()
    if len(quoted) > 240:
        quoted = quoted[:237] + "..."
    resolved = bool(comment.get("resolved", False))

    attribution_parts: list[str] = [author]
    if resolved:
        attribution_parts.append("resolved")
    if not anchored and quoted:
        attribution_parts.append("anchor not found")

    heading_number = str(num)
    if anchored:
        heading_number += f", {{{{mdspec-typst:#ref(<{_comment_body_label(num)}>, form: \"page\")}}}}"

    out: list[str] = [f"## [{heading_number}] {' · '.join(attribution_parts)}"]
    if quoted:
        out.append("")
        out.append(f"*“{quoted}”*")

    inner: list[str] = []
    if body_text:
        inner.append(body_text)

    valid_replies: list[tuple[str, str]] = []
    for reply in comment.get("replies") or []:
        rbody = (reply.get("content") or "").strip()
        if not rbody:
            continue
        rauthor = (reply.get("author") or {}).get("displayName", "Unknown")
        valid_replies.append((rauthor, rbody))

    if valid_replies:
        if inner:
            inner.append("")
        for i, (rauthor, rbody) in enumerate(valid_replies):
            if i > 0:
                inner.append(">")
            inner.append(f"> *{rauthor}*")
            inner.append(">")
            for line in rbody.splitlines():
                inner.append(f"> {line}")

    if inner:
        out.append("")
        for line in inner:
            out.append(">" if line == "" else f"> {line}")

    return "\n".join(out)


def _comment_body_label(num: int) -> str:
    return f"mdspec-comment-body-{num}"


_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})", re.MULTILINE)


def _shift_past_code_context(body: str, start: int, end: int) -> int:
    """If [start, end) lands inside a fenced or inline code block, return a
    position just after the enclosing block; otherwise return end unchanged."""
    fence_open: Optional[re.Match[str]] = None
    fence_marker: Optional[str] = None
    for match in _FENCE_RE.finditer(body):
        if match.start() >= start:
            break
        marker = match.group(1)[0]
        if fence_open is None:
            fence_open = match
            fence_marker = marker
        elif marker == fence_marker:
            fence_open = None
            fence_marker = None
    if fence_open is not None and fence_marker is not None:
        close_re = re.compile(rf"^[ \t]*{re.escape(fence_marker)}{{3,}}[ \t]*$", re.MULTILINE)
        close = close_re.search(body, fence_open.end())
        if close and close.start() > start:
            newline = body.find("\n", close.end())
            return newline + 1 if newline != -1 else len(body)

    line_start = body.rfind("\n", 0, start) + 1
    if body.count("`", line_start, start) % 2 == 1:
        close = body.find("`", end)
        if close != -1:
            return close + 1
    return end


def fetch_doc_with_comments(spec: str, config_file: Path) -> tuple[str, str, str]:
    doc_id = parse_doc_id(spec)
    creds = run_consent(config_file)
    drive, docs = build_services(creds)
    meta = drive.files().get(fileId=doc_id, fields="name", supportsAllDrives=True).execute()
    drive_name = meta.get("name", doc_id)

    tabs = list_tabs(docs, doc_id)
    if len(tabs) > 1:
        markdown = assemble_tab_markdown(creds, doc_id, tabs)
    else:
        markdown = export_markdown(drive, doc_id)

    comments = fetch_comments(drive, doc_id)
    return doc_id, drive_name, insert_references(markdown, comments)
