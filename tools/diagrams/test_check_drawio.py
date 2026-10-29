from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import check_drawio

HERE = Path(__file__).parent
ICON = "shape=image;aspect=fixed;image=img/lib/azure2/networking/Front_Doors.svg;"


def diagram(*cells: str) -> str:
    return (
        '<mxfile><diagram><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="title" value="Title" style="text;fontSize=34;" vertex="1" parent="1">'
        '<mxGeometry x="0" y="0" width="400" height="40" as="geometry"/></mxCell>'
        + "".join(cells)
        + "</root></mxGraphModel></diagram></mxfile>"
    )


def vertex(
    cid: str, x: int, y: int, w: int, h: int, style: str, value: str = "x"
) -> str:
    return (
        f'<mxCell id="{cid}" value="{value}" style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )


def edge(cid: str, source: str, target: str, style: str = "", points: str = "") -> str:
    return (
        f'<mxCell id="{cid}" style="{style}" edge="1" parent="1" source="{source}" target="{target}">'
        f'<mxGeometry relative="1" as="geometry">{points}</mxGeometry></mxCell>'
    )


def run(tmp_path: Path, xml: str) -> list[str]:
    path = tmp_path / "d.drawio"
    path.write_text(xml, encoding="utf-8")
    return [f"{level} {message}" for level, message in check_drawio.check(path)]


def test_a_clean_diagram_has_no_findings(tmp_path):
    xml = diagram(
        vertex(
            "g",
            0,
            60,
            400,
            300,
            "rounded=1;strokeColor=#999;verticalAlign=top;",
            "Group",
        ),
        vertex("a", 60, 120, 72, 72, ICON),
        vertex("b", 260, 120, 72, 72, ICON),
        edge("e", "a", "b"),
    )
    assert run(tmp_path, xml) == []


def test_overlapping_icons_and_icons_too_close_to_their_group_are_errors(tmp_path):
    xml = diagram(
        vertex(
            "g",
            0,
            60,
            400,
            300,
            "rounded=1;strokeColor=#999;verticalAlign=top;",
            "Group",
        ),
        vertex("a", 5, 120, 72, 72, ICON),
        vertex("b", 40, 130, 72, 72, ICON),
    )
    found = run(tmp_path, xml)
    assert any("icons overlap" in line for line in found)
    assert any("from the border" in line for line in found)


def test_an_arrow_through_an_unrelated_icon_is_reported(tmp_path):
    xml = diagram(
        vertex("a", 20, 100, 72, 72, ICON),
        vertex("middle", 200, 100, 72, 72, ICON),
        vertex("b", 400, 100, 72, 72, ICON),
        edge("e", "a", "b"),
    )
    assert any("runs through" in line for line in run(tmp_path, xml))


def test_a_waypoint_route_around_the_icon_is_accepted(tmp_path):
    xml = diagram(
        vertex("a", 20, 100, 72, 72, ICON),
        vertex("middle", 200, 100, 72, 72, ICON),
        vertex("b", 400, 100, 72, 72, ICON),
        edge(
            "e",
            "a",
            "b",
            "exitX=0.5;exitY=1;entryX=0.5;entryY=1;",
            '<Array as="points"><mxPoint x="56" y="240"/><mxPoint x="436" y="240"/></Array>',
        ),
    )
    assert not any("runs through" in line for line in run(tmp_path, xml))


def test_small_fonts_banned_wording_and_missing_labels_are_errors(tmp_path):
    xml = diagram(
        vertex("t", 0, 0, 200, 30, "text;fontSize=10;", "tiny"),
        vertex("b", 0, 40, 300, 30, "text;fontSize=14;", "Not deployed yet"),
        vertex(
            "d",
            0,
            200,
            300,
            30,
            "text;fontSize=14;",
            "Checked without a live subscription",
        ),
        vertex("c", 0, 80, 300, 30, "text;fontSize=14;", "Made with Claude"),
        vertex("i", 0, 120, 72, 72, ICON, ""),
    )
    found = " | ".join(run(tmp_path, xml))
    for expected in (
        "font size 10",
        "not deployed",
        "live subscription",
        "claude",
        "icon without a label",
    ):
        assert expected.lower() in found.lower()


def test_dashed_elements_need_a_legend(tmp_path):
    dashed = vertex(
        "d",
        0,
        60,
        300,
        200,
        "rounded=1;dashed=1;strokeColor=#00f;verticalAlign=top;",
        "Optional",
    )
    assert any("no legend" in line for line in run(tmp_path, diagram(dashed)))
    legend = vertex("l", 400, 60, 100, 30, "text;fontSize=14;", "Legend")
    assert not any(
        "no legend" in line for line in run(tmp_path, diagram(dashed, legend))
    )


def test_icons_that_are_not_azure_stickers_are_warned(tmp_path):
    other = vertex("o", 0, 60, 72, 72, "shape=image;image=https://example.com/x.svg;")
    assert any(
        line.startswith("WARN") and "official Azure" in line
        for line in run(tmp_path, diagram(other))
    )


def test_a_file_that_is_not_drawio_is_an_error(tmp_path):
    assert run(tmp_path, "<html/>")[0].startswith("ERROR")
    assert run(tmp_path, "not xml")[0].startswith("ERROR")


def test_the_hook_returns_findings_only_for_drawio_files(tmp_path):
    bad = tmp_path / "bad.drawio"
    bad.write_text(
        diagram(vertex("t", 0, 0, 200, 30, "text;fontSize=9;", "tiny")),
        encoding="utf-8",
    )

    def hook(path: Path) -> str:
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(path)}}
        return subprocess.run(
            [sys.executable, str(HERE / "check_drawio.py"), "--hook"],
            input=json.dumps(payload),
            check=False,
            capture_output=True,
            text=True,
        ).stdout

    answer = json.loads(hook(bad))["hookSpecificOutput"]
    assert (
        answer["hookEventName"] == "PostToolUse"
        and "font size 9" in answer["additionalContext"]
    )
    assert hook(tmp_path / "notes.md") == ""


def test_the_diagrams_in_the_repository_pass():
    for name in ("azure-architecture-detailed", "azure-architecture-overview"):
        path = HERE.parent.parent / "docs" / "images" / f"{name}.drawio"
        errors = [p for p in check_drawio.check(path) if p[0] == "ERROR"]
        assert errors == [], f"{name}: {errors}"
