"""Parse repo-relative file paths out of GitLab issue markdown."""

from __future__ import annotations

import re

_BACKTICK = re.compile(r"`([^`\n]{1,512})`")
_MD_LINK = re.compile(r"\[[^\]]{0,256}\]\(([^)]{1,512})\)")
_BARE_FILE = re.compile(
    r"(?<![A-Za-z0-9_])((?:[\w.-]+/){1,24}[\w.-]+\.[A-Za-z0-9]{1,12})"
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


def extract_file_paths(markdown: str | None) -> list[str]:
    """Return unique repo-relative paths mentioned in issue/MR markdown."""
    if not markdown:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for rx in (_BACKTICK, _MD_LINK, _BARE_FILE):
        for match in rx.finditer(markdown):
            cand = _clean_candidate(match.group(1))
            if not _looks_like_path(cand) or cand in seen:
                continue
            seen.add(cand)
            found.append(cand)
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
