"""Parse repo-relative file paths out of GitLab issue markdown."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

_BACKTICK = re.compile(r"`([^`\n]{1,512})`")
_MD_LINK = re.compile(r"\[[^\]]{0,256}\]\(([^)]{1,512})\)")
_BARE_FILE = re.compile(
    r"(?<![A-Za-z0-9_])((?:[\w.-]+/){1,24}[\w.-]+\.[A-Za-z0-9]{1,12})"
)
_GITLAB_BLOB = re.compile(
    r"/(?:-/)?blob/[^/]+/(.+?)(?:#|\?|$)",
    re.IGNORECASE,
)
_GITLAB_TREE = re.compile(
    r"/(?:-/)?tree/[^/]+/(.+?)(?:#|\?|$)",
    re.IGNORECASE,
)
_SKIP_SCHEMES = ("http://", "https://", "mailto:", "#")


def _clean_candidate(raw: str) -> str:
    text = (raw or "").strip().strip("\"'")
    text = text.split("?", 1)[0].split("#", 1)[0]
    text = text.replace("\\", "/").lstrip("./")
    if text.startswith("/"):
        text = text.lstrip("/")
    return text


def _looks_like_path(text: str) -> bool:
    if not text or len(text) > 512:
        return False
    lower = text.lower()
    if any(lower.startswith(s) for s in _SKIP_SCHEMES):
        return False
    if " " in text or "\n" in text:
        return False
    if "/" not in text and "." not in text:
        return False
    if ".." in text.split("/"):
        return False
    return True


def _path_from_gitlab_url(url: str, *, host: str = "", project: str = "") -> str | None:
    text = unquote((url or "").strip())
    if not text:
        return None
    for rx in (_GITLAB_BLOB, _GITLAB_TREE):
        match = rx.search(text)
        if match:
            cand = _clean_candidate(match.group(1))
            return cand if _looks_like_path(cand) else None
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        return None
    path = parsed.path or ""
    host_bits = (host or "").replace("https://", "").replace("http://", "").strip("/")
    if host_bits and host_bits not in (parsed.netloc or ""):
        return None
    if project:
        proj = project.strip("/")
        marker = f"/{proj}/-/"
        if marker not in path and f"/{proj}/" not in path:
            return None
    for rx in (_GITLAB_BLOB, _GITLAB_TREE):
        match = rx.search(path)
        if match:
            cand = _clean_candidate(match.group(1))
            return cand if _looks_like_path(cand) else None
    return None


def extract_file_paths(markdown: str | None) -> list[str]:
    """Return unique repo-relative paths mentioned in issue/MR markdown."""
    return collect_paths_from_text(markdown)


def collect_paths_from_text(
    markdown: str | None,
    *,
    host: str = "",
    project: str = "",
) -> list[str]:
    """Return unique repo-relative paths from markdown, URLs, and bare paths."""
    if not markdown:
        return []
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        cand = _clean_candidate(raw)
        if not _looks_like_path(cand) or cand in seen:
            return
        seen.add(cand)
        found.append(cand)

    for rx in (_BACKTICK, _MD_LINK, _BARE_FILE):
        for match in rx.finditer(markdown):
            add(match.group(1))
    for token in re.findall(r"https?://[^\s)>\"']+", markdown):
        from_url = _path_from_gitlab_url(token, host=host, project=project)
        if from_url:
            add(from_url)
    return found


def bind_matches_name(
    names: list[str],
    mentioned: list[str],
    *,
    folder_rel: str,
    repo_prefix: str,
) -> dict[str, list[str]]:
    """Map listing names to mentioned paths that refer to them."""
    prefix = (repo_prefix or "").replace("\\", "/").strip("/")
    folder = (folder_rel or "").replace("\\", "/").strip("/")
    hits: dict[str, list[str]] = {n: [] for n in names}
    for mention in mentioned:
        m = mention.replace("\\", "/").strip("/")
        if prefix and m.startswith(prefix + "/"):
            m = m[len(prefix) + 1 :]
        if folder and m.startswith(folder + "/"):
            m = m[len(folder) + 1 :]
        base = m.split("/")[-1] if "/" in m else m
        for name in names:
            if name == base or name == m or m.endswith("/" + name):
                if mention not in hits[name]:
                    hits[name].append(mention)
    return hits
