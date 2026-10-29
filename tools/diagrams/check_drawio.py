"""Check a draw.io diagram against the rules in docs/DIAGRAMS.md.

    python tools/diagrams/check_drawio.py docs/images/azure-architecture-detailed.drawio
    python tools/diagrams/check_drawio.py --hook        # Claude Code PostToolUse hook, reads JSON on stdin

Exit code 1 when there is an ERROR. Checks what a machine can check (structure, overlaps, margins,
arrows through icons, small fonts, missing legend, banned wording). Whether every label is true is
a review step: see "Accuracy" in the guide.
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

MIN_FONT = 12
MARGIN = 20  # icon to the border of the group that holds it
ICON_PREFIXES = ("img/lib/azure2/", "img/lib/mscae/", "data:image/")
BANNED = re.compile(
    r"claude|anthropic|not (?:yet )?deployed|never (?:been )?deployed|live subscription"
    r"|non ancora distribuit|without a subscription",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h

    def overlaps(self, other: Box) -> bool:
        return not (
            self.x + self.w <= other.x
            or other.x + other.w <= self.x
            or self.y + self.h <= other.y
            or other.y + other.h <= self.y
        )

    def shrunk(self, by: float) -> Box:
        return Box(self.x + by, self.y + by, self.w - 2 * by, self.h - 2 * by)

    def margin_inside(self, inner: Box) -> float:
        return min(
            inner.x - self.x,
            inner.y - self.y,
            self.x + self.w - inner.x - inner.w,
            self.y + self.h - inner.y - inner.h,
        )


@dataclass
class Cell:
    id: str
    value: str
    style: dict[str, str]
    vertex: bool
    edge: bool
    source: str | None
    target: str | None
    box: Box | None
    points: list[tuple[float, float]]
    raw_style: str


def parse_style(text: str) -> dict[str, str]:
    result = {}
    for part in text.split(";"):
        if part:
            key, _, value = part.partition("=")
            result[key] = value
    return result


def load(path: Path) -> list[Cell]:
    root = ET.parse(path).getroot()
    model = root.find(".//mxGraphModel")
    if model is None:
        raise ValueError("no mxGraphModel: not a draw.io file")
    cells = []
    for node in model.iter("mxCell"):
        geometry = node.find("mxGeometry")
        box = None
        points: list[tuple[float, float]] = []
        if geometry is not None:
            if node.get("vertex") == "1":
                box = Box(
                    *(float(geometry.get(k, 0)) for k in ("x", "y", "width", "height"))
                )
            points = [
                (float(p.get("x", 0)), float(p.get("y", 0)))
                for p in geometry.findall("./Array/mxPoint")
            ]
        raw = node.get("style", "")
        cells.append(
            Cell(
                node.get("id", ""),
                node.get("value", ""),
                parse_style(raw),
                node.get("vertex") == "1",
                node.get("edge") == "1",
                node.get("source"),
                node.get("target"),
                box,
                points,
                raw,
            )
        )
    return cells


def plain(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value).replace("&nbsp;", " ")


def is_icon(cell: Cell) -> bool:
    return cell.vertex and cell.style.get("shape") == "image"


def is_group(cell: Cell) -> bool:
    box = cell.box
    return bool(
        cell.vertex
        and box
        and not is_icon(cell)
        and cell.style.get("strokeColor") != "none"
        and not cell.raw_style.startswith("text;")
        and box.w >= 200
        and box.h >= 100
    )


def font_sizes(cell: Cell) -> list[float]:
    sizes = [float(cell.style["fontSize"])] if "fontSize" in cell.style else []
    sizes += [
        float(m) for m in re.findall(r"font-size:\s*(\d+(?:\.\d+)?)px", cell.value)
    ]
    return sizes


def segments(
    cell: Cell, by_id: dict[str, Cell]
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Straight pieces of an arrow that can be known without running draw.io's router."""
    source, target = by_id.get(cell.source or ""), by_id.get(cell.target or "")
    if not (source and target and source.box and target.box):
        return []

    def anchor(item: Cell, kind: str) -> tuple[float, float]:
        b = item.box
        assert b is not None
        fx, fy = item.style.get(f"{kind}X"), None
        fy = cell.style.get(f"{kind}Y")
        fx = cell.style.get(f"{kind}X")
        if fx is None or fy is None:
            return (b.x + b.w / 2, b.y + b.h / 2)
        return (b.x + float(fx) * b.w, b.y + float(fy) * b.h)

    start, end = anchor(source, "exit"), anchor(target, "entry")
    path = [start, *cell.points, end]
    pieces = []
    for a, b in pairwise(path):
        if (
            abs(a[0] - b[0]) < 3 or abs(a[1] - b[1]) < 3
        ):  # only axis-aligned pieces are certain
            pieces.append((a, b))
    return pieces


def hits(piece: tuple[tuple[float, float], tuple[float, float]], box: Box) -> bool:
    (x1, y1), (x2, y2) = piece
    segment = Box(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
    return (
        segment.overlaps(box)
        or (segment.w == 0 and box.contains(x1, (y1 + y2) / 2))
        or (segment.h == 0 and box.contains((x1 + x2) / 2, y1))
    )


def check(path: Path) -> list[tuple[str, str]]:
    problems: list[tuple[str, str]] = []

    def add(level: str, where: str, message: str) -> None:
        problems.append((level, f"{where}: {message}"))

    try:
        cells = load(path)
    except (ET.ParseError, ValueError) as error:
        return [("ERROR", f"{path.name}: {error}")]
    by_id = {cell.id: cell for cell in cells}
    icons = [c for c in cells if is_icon(c) and c.box]
    groups = [c for c in cells if is_group(c)]

    for cell in cells:
        text = plain(cell.value)
        if match := BANNED.search(text):
            add(
                "ERROR",
                cell.id,
                f"wording not allowed in a diagram: '{match.group(0)}'",
            )
        for size in font_sizes(cell):
            if size < MIN_FONT:
                add("ERROR", cell.id, f"font size {size:g} is below {MIN_FONT}")
    for icon in icons:
        image = icon.style.get("image", "")
        if not plain(icon.value).strip():
            add("ERROR", icon.id, "icon without a label")
        if not image.startswith(ICON_PREFIXES):
            add(
                "WARN",
                icon.id,
                f"icon is not an official Azure sticker: {image[:40] or 'none'}",
            )

    for i, a in enumerate(icons):
        for b in icons[i + 1 :]:
            assert a.box and b.box
            if a.box.overlaps(b.box):
                add("ERROR", f"{a.id}/{b.id}", "icons overlap")
    for icon in icons:
        assert icon.box
        holders = [
            g
            for g in groups
            if g.box
            and g.box.contains(icon.box.x + icon.box.w / 2, icon.box.y + icon.box.h / 2)
        ]
        if holders:
            inner = min(holders, key=lambda g: g.box.w * g.box.h if g.box else 0)
            assert inner.box
            if (gap := inner.box.margin_inside(icon.box)) < MARGIN:
                add(
                    "ERROR",
                    icon.id,
                    f"only {gap:.0f}px from the border of '{plain(inner.value).strip()[:30]}' (needs {MARGIN})",
                )

    for cell in cells:
        if not cell.edge:
            continue
        floating = cell.source is None and cell.target is None
        if not floating and (cell.source not in by_id or cell.target not in by_id):
            add("ERROR", cell.id, "arrow with a missing source or target")
            continue
        for piece in segments(cell, by_id):
            for icon in icons:
                if icon.id in (cell.source, cell.target) or not icon.box:
                    continue
                if hits(piece, icon.box.shrunk(4)):
                    add(
                        "WARN",
                        cell.id,
                        f"arrow runs through the icon '{plain(icon.value).strip()[:30]}'",
                    )

    dashed = any(c.style.get("dashed") == "1" for c in cells if c.id != "canvas")
    if dashed and not any("legend" in plain(c.value).lower() for c in cells):
        add("ERROR", path.name, "dashed elements but no legend")
    if not any(
        c.vertex and c.style.get("fontSize") and float(c.style["fontSize"]) >= 28
        for c in cells
    ):
        add("WARN", path.name, "no title (a text cell with font size 28 or more)")
    return problems


def report(path: Path) -> int:
    problems = check(path)
    for level, message in problems:
        print(f"{level} {message}")
    errors = sum(level == "ERROR" for level, _ in problems)
    print(f"{path.name}: {errors} error(s), {len(problems) - errors} warning(s)")
    return 1 if errors else 0


def hook() -> int:
    """PostToolUse: after a .drawio file is written, hand the findings back to the model."""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    target = (payload.get("tool_input") or {}).get("file_path") or ""
    if not target.endswith(".drawio") or not Path(target).is_file():
        return 0
    problems = check(Path(target))
    if problems:
        lines = "\n".join(f"{level} {message}" for level, message in problems)
        context = (
            f"draw.io check of {Path(target).name} (rules: docs/DIAGRAMS.md). Fix every ERROR, "
            f"read each WARN, re-render and look at the picture:\n{lines}"
        )
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": context,
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--hook"]:
        raise SystemExit(hook())
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    raise SystemExit(max(report(Path(p)) for p in sys.argv[1:]))
