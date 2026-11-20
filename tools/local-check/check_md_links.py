"""Fail when a Markdown file links to a relative path that does not exist.

Usage: python check_md_links.py FILE...
URLs (any scheme) and pure #anchors are skipped; #fragments are stripped.
"""

import re
import sys
from pathlib import Path

LINK = re.compile(r"\]\(([^)]+)\)")
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def broken_links(path: Path) -> list[tuple[int, str]]:
    found = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        for raw in LINK.findall(line):
            # drop an optional link title: [x](file.md "title")
            target = raw.strip().split(" ")[0].strip("<>")
            if not target or target.startswith("#") or SCHEME.match(target):
                continue
            target = target.split("#", 1)[0]
            if target and not (path.parent / target).exists():
                found.append((lineno, target))
    return found


def main(argv: list[str]) -> int:
    bad = 0
    for name in argv:
        for lineno, target in broken_links(Path(name)):
            print(f"{name}:{lineno}: missing target {target}")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
