#!/usr/bin/env python3
"""Scan tracked files for hardcoded credentials. Exit 1 on findings."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

# High-confidence assignments / material. Placeholders and env lookups are ignored.
PATTERNS = [
    re.compile(r"""(?:BASE_TOKEN|LARK_BASE_APP_TOKEN)\s*=\s*['"][A-Za-z0-9_\-]{16,}['"]"""),
    re.compile(r"""(?:API_KEY|ACCESS_TOKEN|APP_SECRET|PRIVATE_KEY|PASSWORD)\s*=\s*['"][^'"]{12,}['"]"""),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"shpat_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
]

PLACEHOLDER_HINTS = (
    "your_",
    "changeme",
    "placeholder",
    "example",
    "<secret",
    "xxx",
    "todo",
    "redacted",
)

SKIP_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".woff",
    ".woff2",
    ".sqlite",
    ".db",
)


def _tracked_files() -> List[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    names = [n for n in result.stdout.decode("utf-8", errors="replace").split("\0") if n]
    return [REPO_ROOT / name for name in names]


def _is_placeholder(line: str) -> bool:
    lowered = line.lower()
    return any(hint in lowered for hint in PLACEHOLDER_HINTS)


def scan_lines(lines: Iterable[str], path: Path) -> List[Tuple[str, int]]:
    findings: List[Tuple[str, int]] = []
    for index, line in enumerate(lines, start=1):
        if _is_placeholder(line):
            continue
        for pattern in PATTERNS:
            if pattern.search(line):
                findings.append((str(path.relative_to(REPO_ROOT)), index))
                break
    return findings


def scan_files(files: Iterable[Path]) -> List[Tuple[str, int]]:
    findings: List[Tuple[str, int]] = []
    for path in files:
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(scan_lines(text.splitlines(), path))
    return findings


def main() -> int:
    findings = scan_files(_tracked_files())
    if findings:
        print("Secret scan failed. Possible hardcoded credentials:")
        for rel, line in findings:
            print(f"  {rel}:{line}")
        return 1
    print("Secret scan passed (no high-confidence hardcoded credentials in tracked files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
