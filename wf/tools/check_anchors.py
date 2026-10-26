#!/usr/bin/env python3
"""Check fragments in local Markdown links."""

from __future__ import annotations

import argparse
from collections import defaultdict
import configparser
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
from urllib.parse import unquote


FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
LINK_RE = re.compile(r"(?<!!)\[[^]\n]*\]\(([^)\n]+)\)")
TAG_RE = re.compile(r"<[^>\n]+>")
ANCHOR_ATTR_RE = re.compile(r"\b(?:id|name)\s*=\s*(['\"])(.*?)\1", re.I)
SKIP_PARTS = {
    "archive",
    ".git",
    "node_modules",
    "__pycache__",
    "reference",
    "references",
    "vendor",
    "done",
    "superseded",
}


def without_code_spans(line: str) -> str:
    """Blank matched backtick spans while preserving character positions."""
    result = line
    position = 0
    while match := re.search(r"`+", result[position:]):
        start = position + match.start()
        marker = match.group()
        closing = re.search(
            rf"(?<!`){re.escape(marker)}(?!`)", result[start + len(marker) :]
        )
        if not closing:
            position = start + len(marker)
            continue
        end = start + len(marker) + closing.end()
        result = result[:start] + " " * (end - start) + result[end:]
        position = end
    return result


def github_heading_slug(text: str) -> str:
    text = re.sub(r"!?\[([^]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unicodedata.normalize("NFKC", text).strip().lower()
    text = "".join(
        char
        for char in text
        if unicodedata.category(char)[0] in "LMN"
        or char in "-_"
        or char.isspace()
    )
    return re.sub(r"\s", "-", text)


def markdown_anchors(markdown: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = defaultdict(int)
    fence: str | None = None
    for line in markdown.read_text(encoding="utf-8").splitlines():
        marker = FENCE_RE.match(line)
        if marker:
            current = marker.group(1)
            if fence is None:
                fence = current
            elif fence == current:
                fence = None
            continue
        if fence is not None:
            continue
        for tag in TAG_RE.findall(line):
            anchors.update(match.group(2) for match in ANCHOR_ATTR_RE.finditer(tag))
        heading = HEADING_RE.match(line)
        if not heading:
            continue
        text = re.sub(r"\s+#+\s*$", "", heading.group(1))
        slug = github_heading_slug(text)
        if not slug:
            continue
        number = counts[slug]
        counts[slug] += 1
        anchors.add(slug if number == 0 else f"{slug}-{number}")
    return anchors


def markdown_links(markdown: Path):
    fence: str | None = None
    for line_number, line in enumerate(
        markdown.read_text(encoding="utf-8").splitlines(), 1
    ):
        marker = FENCE_RE.match(line)
        if marker:
            current = marker.group(1)
            if fence is None:
                fence = current
            elif fence == current:
                fence = None
            continue
        if fence is not None:
            continue
        for match in LINK_RE.finditer(without_code_spans(line)):
            target = re.sub(r'\s+"[^"]*"\s*$', "", match.group(1).strip())
            if "#" not in target:
                continue
            lowered = target.lower()
            if target.startswith("<") or lowered.startswith(
                ("http://", "https://", "mailto:")
            ):
                continue
            path, _, fragment = target.partition("#")
            yield line_number, path, unquote(fragment)


def submodule_paths(root: Path) -> set[tuple[str, ...]]:
    modules = root / ".gitmodules"
    if not modules.is_file():
        return set()
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(modules, encoding="utf-8")
    except configparser.Error:
        return set()
    paths: set[tuple[str, ...]] = set()
    for section in config.sections():
        if not section.startswith("submodule ") or not config.has_option(section, "path"):
            continue
        path = Path(config.get(section, "path"))
        if path.is_absolute() or ".." in path.parts:
            continue
        paths.add(path.parts)
    return paths


def _is_mail_inbox(parent_parts: tuple[str, ...], directory: str) -> bool:
    """放信的 inbox/（寄件原文不改，比照 archive）；workflows/inbox/ 是工作流文件，照掃。"""
    return directory == "inbox" and parent_parts[-1:] != ("workflows",)


def markdown_files(root: Path) -> list[Path]:
    submodules = submodule_paths(root)
    markdown: list[Path] = []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        relative_parts = current_path.relative_to(root).parts
        dirs[:] = [
            directory
            for directory in dirs
            if directory not in SKIP_PARTS
            and not _is_mail_inbox(relative_parts, directory)
            and relative_parts + (directory,) not in submodules
        ]
        markdown.extend(
            current_path / filename for filename in files if filename.endswith(".md")
        )
    return sorted(drop_git_ignored(root, markdown))


def drop_git_ignored(root: Path, files: list[Path]) -> list[Path]:
    """git repo 內丟掉 .gitignore 命中的檔（tracked 不算）；非 repo 或 root 本身被 ignore 時照舊。"""
    if not files:
        return files

    def git(*args: str, data: bytes | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(root), *args], input=data, capture_output=True
        )

    try:
        inside = git("rev-parse", "--is-inside-work-tree")
    except OSError:
        return files
    if inside.returncode != 0 or git("check-ignore", "-q", ".").returncode == 0:
        return files
    relative = [str(path.relative_to(root)) for path in files]
    result = git(
        "check-ignore", "-z", "--stdin", data="\0".join(relative).encode() + b"\0"
    )
    ignored = {item.decode() for item in result.stdout.split(b"\0") if item}
    return [path for path, rel in zip(files, relative) if rel not in ignored]


def check_root(root: Path) -> list[tuple[str, int, str, str]]:
    root = root.resolve()
    broken: list[tuple[str, int, str, str]] = []
    cache: dict[Path, set[str]] = {}
    for source in markdown_files(root):
        source_name = str(source.relative_to(root))
        for line_number, path, fragment in markdown_links(source):
            target = source if not path else (source.parent / path).resolve()
            if not target.exists() or not target.is_file() or target.suffix.lower() != ".md":
                continue
            anchors = cache.setdefault(target, markdown_anchors(target))
            if fragment not in anchors:
                target_name = os.path.relpath(target, root)
                broken.append((source_name, line_number, target_name, fragment))
    return broken


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", type=Path)
    args = parser.parse_args(argv)
    broken = []
    for root in args.roots or [Path(".")]:
        broken.extend(check_root(root))
    for source, line, target, fragment in broken:
        print(f"BROKEN-ANCHOR {source}:{line} -> {target}#{fragment}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
