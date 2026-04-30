"""Google Drive integration: fetch a Doc as Markdown plus its comment threads."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

DEFAULT_CREDENTIALS_PATH = Path.home() / ".gt-headroom-mcp" / "gcp-oauth.keys.json"

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


def credentials_path(override: Optional[Path] = None) -> Path:
    if override:
        return override.expanduser()
    env = os.environ.get("MDSPEC_GOOGLE_CREDENTIALS")
    if env:
        return Path(env).expanduser()
    return DEFAULT_CREDENTIALS_PATH


def token_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "mdspec" / "google-token.json"


def run_consent(credentials_file: Path, force: bool = False):
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
        if not credentials_file.exists():
            sys.exit(
                f"error: OAuth client config not found at {credentials_file}\n"
                f"set MDSPEC_GOOGLE_CREDENTIALS or pass --credentials"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
        creds = flow.run_local_server(port=0)
        tok_path.parent.mkdir(parents=True, exist_ok=True)
        tok_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def get_drive_service(credentials_file: Path):
    from googleapiclient.discovery import build

    creds = run_consent(credentials_file)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def export_markdown(service, doc_id: str) -> str:
    data: bytes = service.files().export(fileId=doc_id, mimeType="text/markdown").execute()
    return data.decode("utf-8")


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
    if not comments:
        return markdown

    body = markdown
    references: list[str] = []

    for index, comment in enumerate(comments, start=1):
        quoted = (comment.get("quotedFileContent") or {}).get("value", "")
        anchored = False
        if quoted:
            position = body.find(quoted)
            if position != -1:
                end = position + len(quoted)
                body = body[:end] + f" \\[{index}\\]" + body[end:]
                anchored = True
            else:
                print(
                    f"warning: comment [{index}] anchor not found in markdown export",
                    file=sys.stderr,
                )
        references.append(_format_reference(index, comment, anchored))

    if not references:
        return body

    return body.rstrip() + "\n\n# References\n\n" + "\n\n".join(references) + "\n"


def _format_reference(num: int, comment: dict, anchored: bool) -> str:
    author = (comment.get("author") or {}).get("displayName", "Unknown")
    created = comment.get("createdTime", "")
    content = (comment.get("content") or "").strip()
    quoted = (comment.get("quotedFileContent") or {}).get("value", "").strip()
    resolved = bool(comment.get("resolved", False))

    lines: list[str] = [f"## [{num}]"]
    if quoted:
        excerpt = quoted if len(quoted) <= 240 else quoted[:237] + "..."
        lines.append(f"> {excerpt}")
        lines.append("")
    status = []
    if resolved:
        status.append("resolved")
    if not anchored and quoted:
        status.append("anchor not found")
    suffix = f" — {', '.join(status)}" if status else ""
    lines.append(f"**{author}** ({created}){suffix}")
    lines.append("")
    lines.append(content or "_(no body)_")

    for reply in comment.get("replies") or []:
        rauthor = (reply.get("author") or {}).get("displayName", "Unknown")
        rcreated = reply.get("createdTime", "")
        rbody = (reply.get("content") or "").strip()
        lines.append("")
        lines.append(f"**{rauthor}** ({rcreated})")
        lines.append("")
        lines.append(rbody or "_(no body)_")

    return "\n".join(lines)


def fetch_doc_with_comments(spec: str, credentials_file: Path) -> tuple[str, str, str]:
    doc_id = parse_doc_id(spec)
    service = get_drive_service(credentials_file)
    meta = service.files().get(fileId=doc_id, fields="name").execute()
    drive_name = meta.get("name", doc_id)
    markdown = export_markdown(service, doc_id)
    comments = fetch_comments(service, doc_id)
    return doc_id, drive_name, insert_references(markdown, comments)
