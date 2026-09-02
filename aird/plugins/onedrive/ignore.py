"""Gitignore-style matching for OneDrive folder maps."""

from __future__ import annotations

import os
from fnmatch import fnmatch


def parse_ignore_lines(text: str | None) -> list[str]:
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
        if len(out) >= 400:
            break
    return out


def load_gitignore_file(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return parse_ignore_lines(fh.read())
    except OSError:
        return []


def _match_segment(name: str, pat: str) -> bool:
    return fnmatch(name, pat)


def _match_path(rel: str, pattern: str) -> bool:
    rel = rel.replace("\\", "/").strip("/")
    pat = pattern.replace("\\", "/").strip("/")
    if not pat:
        return False
    if "**" in pat:
        parts = pat.split("**")
        if len(parts) == 2:
            left, right = parts[0].strip("/"), parts[1].strip("/")
            if left and not (rel == left or rel.startswith(left + "/")):
                return False
            rest = rel[len(left) :].lstrip("/") if left else rel
            if not right:
                return True
            if fnmatch(rest, right) or fnmatch(os.path.basename(rel), right):
                return True
            for i, _ch in enumerate(rest):
                if rest[i] == "/" and fnmatch(rest[i + 1 :], right):
                    return True
            return fnmatch(rest, right)
    if "/" in pat.strip("/"):
        return fnmatch(rel, pat) or rel == pat
    base = os.path.basename(rel)
    if _match_segment(base, pat):
        return True
    return any(_match_segment(part, pat) for part in rel.split("/"))


class IgnoreMatcher:
    """Last matching gitignore rule wins. Extra patterns apply after file rules."""

    def __init__(self, rules: list[tuple[str, bool, bool]]):
        # (pattern, negated, dir_only)
        self.rules = rules

    @classmethod
    def from_lines(cls, lines: list[str]) -> IgnoreMatcher:
        rules: list[tuple[str, bool, bool]] = []
        for line in lines:
            negated = line.startswith("!")
            body = line[1:] if negated else line
            dir_only = body.endswith("/")
            body = body.rstrip("/")
            if body.startswith("/"):
                body = body.lstrip("/")
            if body:
                rules.append((body, negated, dir_only))
        return cls(rules)

    def ignored(self, rel_path: str, is_dir: bool = False) -> bool:
        rel = rel_path.replace("\\", "/").strip("/")
        if not rel:
            return False
        ignored = False
        for pattern, negated, dir_only in self.rules:
            if dir_only and not is_dir:
                parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
                if parent and _match_path(parent, pattern):
                    ignored = not negated
                continue
            if _match_path(rel, pattern):
                ignored = not negated
        return ignored


def matcher_for_folder(folder: str, extra_text: str | None = None) -> IgnoreMatcher:
    lines = load_gitignore_file(os.path.join(folder, ".gitignore"))
    lines.extend(parse_ignore_lines(extra_text))
    nested: list[str] = []
    try:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules"}]
            if ".gitignore" in files and os.path.normpath(root) != os.path.normpath(folder):
                rel_base = os.path.relpath(root, folder).replace("\\", "/")
                for pat in load_gitignore_file(os.path.join(root, ".gitignore")):
                    if pat.startswith("!"):
                        nested.append("!" + rel_base + "/" + pat[1:].lstrip("/"))
                    else:
                        nested.append(rel_base + "/" + pat.lstrip("/"))
    except OSError:
        pass
    lines.extend(nested)
    return IgnoreMatcher.from_lines(lines)
