"""Writes docs/images/azure-architecture.drawio, the editable draw.io version of the Azure
architecture (docs/AZURE-ARCHITECTURE.md). Run it again after the architecture changes:

    python tools/diagrams/azure_architecture_drawio.py
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import quoteattr

OUT = Path(__file__).resolve().parents[2] / "docs" / "images" / "azure-architecture.drawio"

FONT = "fontFamily=Helvetica;fontSize=14;"
COLORS = {
    "service": "fillColor=#DEEBF7;strokeColor=#0078D4;fontColor=#10243B;",
    "data": "fillColor=#E2F0D9;strokeColor=#548235;fontColor=#10243B;",
    "ai": "fillColor=#FCE4D6;strokeColor=#C55A11;fontColor=#10243B;",
    "secure": "fillColor=#EDE7F6;strokeColor=#5B3F9C;fontColor=#10243B;",
    "user": "fillColor=#FFF2CC;strokeColor=#BF9000;fontColor=#10243B;",
    "standby": "fillColor=#F2F2F2;strokeColor=#7F7F7F;fontColor=#10243B;dashed=1;",
}

frames: list[str] = []  # behind everything
cells: list[str] = []
edges: list[str] = []  # above the frames, below the boxes
_next = [1]


def _id() -> str:
    _next[0] += 1
    return f"c{_next[0]}"


def box(x, y, w, h, label, kind="service", name=None):
    cid = name or _id()
    style = f"rounded=1;whiteSpace=wrap;html=1;arcSize=8;strokeWidth=2;{FONT}{COLORS[kind]}"
    cells.append(
        f'<mxCell id="{cid}" value={quoteattr(label)} style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )
    return cid


def panel(x, y, w, h, title, stroke="#7F8FA6", dashed=False):
    """A frame with its title at the top; contents start 50px below y."""
    style = (
        f"rounded=1;whiteSpace=wrap;html=1;arcSize=3;strokeWidth=2;strokeColor={stroke};"
        "fillColor=none;verticalAlign=top;align=left;spacingLeft=14;spacingTop=8;"
        f"fontStyle=1;fontSize=16;{'dashed=1;' if dashed else ''}fontFamily=Helvetica;fontColor={stroke};"
    )
    frame = _id()
    frames.append(
        f'<mxCell id="{frame}" value={quoteattr(title)} style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )
    return frame


def label(x, y, w, h, text, size=12, color="#44546A", bold=False, align="center"):
    style = (
        f"text;html=1;whiteSpace=wrap;align={align};verticalAlign=middle;fontSize={size};"
        f"fontColor={color};fontFamily=Helvetica;{'fontStyle=1;' if bold else ''}"
    )
    cells.append(
        f'<mxCell id="{_id()}" value={quoteattr(text)} style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )


def edge(source, target, text="", color="#0078D4", dashed=False, points=(), exit_xy=None, entry_xy=None):
    style = (
        "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeWidth=2;"
        f"strokeColor={color};fontSize=11;fontColor=#10243B;fontFamily=Helvetica;"
        f"labelBackgroundColor=#FFFFFF;jumpStyle=arc;jumpSize=8;{'dashed=1;' if dashed else ''}"
    )
    if exit_xy:
        style += f"exitX={exit_xy[0]};exitY={exit_xy[1]};exitDx=0;exitDy=0;"
    if entry_xy:
        style += f"entryX={entry_xy[0]};entryY={entry_xy[1]};entryDx=0;entryDy=0;"
    waypoints = "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in points)
    geometry = f'<Array as="points">{waypoints}</Array>' if waypoints else ""
    edges.append(
        f'<mxCell id="{_id()}" value={quoteattr(text)} style="{style}" edge="1" parent="1" '
        f'source="{source}" target="{target}"><mxGeometry relative="1" as="geometry">{geometry}'
        "</mxGeometry></mxCell>"
    )


def build() -> str:
    label(30, 10, 1500, 40, "NODO AI on Azure: layered architecture", 26, "#10243B", True, "left")
    label(
        30, 48, 1700, 24,
        "Primary region swedencentral, standby in a paired EU region. Every box is Terraform "
        "(infra/terraform/modules/nodo). Not yet applied to a real subscription (docs/FIRST-DEPLOY.md).",
        13, "#44546A", False, "left",
    )

    # ---------------------------------------------------------------- people and the desktop
    panel(30, 100, 250, 520, "People", "#BF9000")
    box(50, 150, 210, 90, "NODO app<br>(desktop window,<br>Android)", "user", "app")
    box(50, 290, 210, 70, "Entra ID<br>sign-in (PKCE)", "secure", "entra")
    box(50, 410, 210, 110, "Local engine<br>SQLite, Whisper,<br>Qwen3, E5 (same features)", "user", "local")
    label(50, 530, 210, 70, "The local engine copies finished meetings to the cloud: PUT /v1/sync (idempotent).", 11)

    # ---------------------------------------------------------------- edge
    panel(320, 100, 290, 520, "01  Edge and identity", "#0078D4")
    box(345, 150, 240, 90, "Front Door Premium<br>+ WAF (bot, rate limits)", "service", "afd")
    box(345, 300, 240, 130, "API Management<br>one gateway: JWT check,<br>rate limit, policies,<br>request id", "service", "apim")
    box(345, 490, 240, 90, "DNS, TLS, Private Link<br>to the gateway", "service", "tls")

    # ---------------------------------------------------------------- primary region
    primary = panel(650, 100, 1100, 700, "Primary region (zone-redundant)", "#0078D4")
    panel(670, 150, 400, 630, "02  Private compute (VNet)", "#0078D4", dashed=True)
    box(695, 200, 350, 100, "Container Apps: API<br>FastAPI, internal ingress,<br>bulkheads, load shedding", "service", "api")
    box(695, 340, 350, 80, "Service Bus Premium<br>queue, scheduled messages, Geo-DR", "service", "sb")
    box(695, 460, 350, 100, "Container Apps job: worker<br>transcribe, analyse, export,<br>poll speech, reanalyse", "service", "worker")
    box(695, 600, 350, 150, "Scheduled jobs (own identity each)<br>retention, sweep, index verify<br>and reindex, audit archive,<br>migrate, preflight", "service", "jobs")

    # lanes between the two panels carry the arrows: x = 1090, 1110, 1130, 1150
    panel(1170, 150, 560, 400, "04  Data (private endpoints, RLS, CMK)", "#548235", dashed=True)
    box(1195, 200, 510, 110, "PostgreSQL Flexible Server (HA, pgvector)<br>meetings, transcripts, graph, entity vectors,<br>answer cache, jobs, audit; row level security", "data", "pg")
    box(1195, 350, 240, 80, "Blob Storage<br>audio, CMK, RA-GZRS<br>(read by the worker)", "data", "blob")
    box(1465, 350, 240, 80, "AI Search<br>hybrid text + vector<br>(index written by the worker)", "data", "search")
    box(1195, 460, 240, 70, "Audit account (WORM)<br>hash-chained batches", "data", "audit")
    box(1465, 460, 240, 70, "Key Vault Premium<br>keys, secrets", "secure", "kv")

    panel(1170, 580, 560, 200, "03  AI gateway and models", "#C55A11", dashed=True)
    box(1195, 640, 160, 110, "APIM AI gateway<br>token budgets,<br>failover between regions", "ai", "aigw")
    box(1370, 640, 160, 110, "Azure OpenAI<br>chat, extraction,<br>embeddings (2 regions)", "ai", "oai")
    box(1545, 640, 160, 110, "Speech<br>batch transcription,<br>diarization", "ai", "speech")

    # ---------------------------------------------------------------- standby
    standby = panel(650, 860, 1100, 110, "05  Standby region (warm)", "#7F7F7F", dashed=True)
    box(675, 905, 250, 50, "Service Bus Geo-DR alias", "standby")
    box(945, 905, 250, 50, "Container Apps (scaled low)", "standby")
    box(1215, 905, 250, 50, "Storage secondary (RA-GZRS)", "standby")
    box(1485, 905, 240, 50, "PostgreSQL read replica", "standby")

    # ---------------------------------------------------------------- cross-cutting
    panel(30, 860, 590, 110, "Cross-cutting", "#5B3F9C")
    label(50, 900, 550, 60,
          "OpenTelemetry to Application Insights, Log Analytics, SLO burn-rate alerts. "
          "Managed identities only, no keys. Defender, Policy (EU regions), locks. "
          "Terraform + GitHub OIDC deploys.", 12, "#10243B", False, "left")

    # ---------------------------------------------------------------- arrows
    edge("app", "afd", "HTTPS", "#0078D4")
    edge("app", "entra", "login", "#5B3F9C", dashed=True)
    edge("local", "afd", "sync", "#BF9000", dashed=True, exit_xy=(1, 0.5), entry_xy=(0, 0.75),
         points=((300, 465), (300, 218)))
    edge("afd", "apim", "private link", "#0078D4")
    edge("apim", "api", "internal ingress", "#0078D4", exit_xy=(1, 0.5), entry_xy=(0, 0.5),
         points=((660, 365), (660, 250)))
    edge("api", "pg", "queries", "#548235", exit_xy=(1, 0.3), entry_xy=(0, 0.3))
    edge("api", "aigw", "", "#C55A11", exit_xy=(1, 0.8), entry_xy=(0, 0.5),
         points=((1130, 280), (1130, 695)))
    edge("aigw", "oai", "", "#C55A11")
    edge("api", "sb", "enqueue", "#0078D4", exit_xy=(0.5, 1), entry_xy=(0.5, 0))
    edge("sb", "worker", "deliver", "#0078D4", exit_xy=(0.5, 1), entry_xy=(0.5, 0))
    edge("worker", "pg", "", "#548235", exit_xy=(1, 0.3), entry_xy=(0, 0.8),
         points=((1090, 490), (1090, 288)))
    edge("worker", "speech", "submit, poll", "#C55A11", exit_xy=(1, 0.7), entry_xy=(0.5, 0),
         points=((1110, 530), (1110, 620), (1625, 620)))
    edge("jobs", "audit", "", "#548235", exit_xy=(1, 0.5), entry_xy=(0, 0.5),
         points=((1150, 675), (1150, 495)))
    edge("app", "blob", "direct upload, write-only link", "#BF9000", dashed=True, exit_xy=(0.5, 0),
         entry_xy=(0, 0.5), points=((155, 88), (1150, 88), (1150, 390)))
    edge(primary, standby, "continuous replication", "#7F7F7F", dashed=True, exit_xy=(0.5, 1),
         entry_xy=(0.5, 0))

    body = "".join(frames + edges + cells)
    return (
        '<mxfile host="app.diagrams.net"><diagram id="azure" name="NODO AI on Azure">'
        '<mxGraphModel dx="1800" dy="1000" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
        'arrows="1" fold="1" page="0" pageScale="1" pageWidth="1800" pageHeight="1000" math="0" shadow="0" '
        'defaultFontFamily="Helvetica"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{body}</root></mxGraphModel></diagram></mxfile>\n"
    )


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8", newline="\n")
    print(OUT)
