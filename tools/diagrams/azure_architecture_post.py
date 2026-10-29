"""Writes docs/images/azure-architecture-post.drawio: the one-picture version of the Azure
architecture with the official Azure icons, in the style of the SecondChance post.

    python tools/diagrams/azure_architecture_post.py

Every box is a resource the Terraform module (infra/terraform/modules/nodo) creates or a rule the
code enforces; nothing is drawn that does not exist. The icons are draw.io's own Azure library
(image=img/lib/azure2/...), so the file shows them in draw.io and in its viewer.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import quoteattr

OUT = Path(__file__).resolve().parents[2] / "docs" / "images" / "azure-architecture-post.drawio"
LIB = "img/lib/azure2/"
INK, MUTED = "#0F172A", "#475569"
KINDS = {  # border colour of a card
    "plain": "#94A3B8",
    "gateway": "#2563EB",
    "data": "#F59E0B",
    "safe": "#16A34A",
    "ai": "#EA580C",
    "ops": "#7C3AED",
    "standby": "#64748B",
}
FONT = "fontFamily=Segoe UI;"
BLUE, ORANGE, GREEN, DARK, PURPLE, GREY = (
    "#2563EB", "#EA580C", "#16A34A", "#334155", "#7C3AED", "#64748B",
)

backdrop: list[str] = []
edges: list[str] = []
cells: list[str] = []
_n = [1]


def _id() -> str:
    _n[0] += 1
    return f"c{_n[0]}"


def band(x, y, w, h, title, stroke="#CBD5E1", fill="#FFFFFF", name=None, dashed=False):
    cid = name or _id()
    style = (
        f"rounded=1;arcSize=2;whiteSpace=wrap;html=1;strokeWidth=2;strokeColor={stroke};"
        f"fillColor={fill};verticalAlign=top;align=left;spacingLeft=26;spacingTop=14;"
        f"fontSize=15;fontStyle=1;letterSpacing=2;fontColor={MUTED};{FONT}"
        f"{'dashed=1;' if dashed else ''}"
    )
    backdrop.append(
        f'<mxCell id="{cid}" value={quoteattr(title)} style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )
    return cid


def card(x, y, w, h, icon, title, lines, kind="plain", name=None, strong=False, dashed=False):
    cid = name or _id()
    body = f'<b style="font-size:18px;color:{INK}">{title}</b>'
    if lines:
        body += f'<br><span style="font-size:13px;color:{MUTED}">{"<br>".join(lines)}</span>'
    image = ""
    if icon:
        image = f"image={LIB}{icon};imageAlign=center;imageVerticalAlign=top;imageWidth=50;imageHeight=50;"
    style = (
        "shape=label;rounded=1;arcSize=9;whiteSpace=wrap;html=1;fillColor=#FFFFFF;"
        f"strokeColor={KINDS[kind]};strokeWidth={4 if strong else 3};{image}"
        f"verticalAlign=bottom;spacingBottom=10;spacingTop=8;spacingLeft=6;spacingRight=6;{FONT}"
        f"{'dashed=1;' if dashed else ''}"
    )
    cells.append(
        f'<mxCell id="{cid}" value={quoteattr(body)} style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )
    return cid


def proof(x, y, w, h, big, lines, color):
    """A guarantee: one big phrase and what stands behind it."""
    body = f'<b style="font-size:22px;color:{color}">{big}</b><br>'
    body += f'<span style="font-size:13px;color:{MUTED}">{"<br>".join(lines)}</span>'
    style = (
        f"rounded=1;arcSize=9;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor={color};"
        f"strokeWidth=3;verticalAlign=middle;spacingLeft=10;spacingRight=10;{FONT}"
    )
    cells.append(
        f'<mxCell id="{_id()}" value={quoteattr(body)} style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )


def text(x, y, w, h, value, size=13, color=MUTED, bold=False, align="left"):
    style = (
        f"text;html=1;whiteSpace=wrap;align={align};verticalAlign=middle;fontSize={size};"
        f"fontColor={color};{'fontStyle=1;' if bold else ''}{FONT}"
    )
    cells.append(
        f'<mxCell id="{_id()}" value={quoteattr(value)} style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )


def badge(cx, cy, number, color):
    style = (
        f"ellipse;whiteSpace=wrap;html=1;fillColor={color};strokeColor=#FFFFFF;strokeWidth=3;"
        f"fontColor=#FFFFFF;fontStyle=1;fontSize=16;{FONT}"
    )
    cells.append(
        f'<mxCell id="{_id()}" value="{number}" style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{cx - 17}" y="{cy - 17}" width="34" height="34" as="geometry"/></mxCell>'
    )


def edge(source, target, color, dashed=False, points=(), exit_xy=None, entry_xy=None, label=""):
    style = (
        "edgeStyle=orthogonalEdgeStyle;rounded=1;curved=0;html=1;endArrow=block;endFill=1;"
        f"endSize=5;strokeWidth=4;strokeColor={color};{'dashed=1;dashPattern=8 6;' if dashed else ''}"
        f"jumpStyle=arc;jumpSize=10;fontSize=13;fontColor={INK};labelBackgroundColor=#FFFFFF;{FONT}"
    )
    if exit_xy:
        style += f"exitX={exit_xy[0]};exitY={exit_xy[1]};exitDx=0;exitDy=0;"
    if entry_xy:
        style += f"entryX={entry_xy[0]};entryY={entry_xy[1]};entryDx=0;entryDy=0;"
    waypoints = "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in points)
    geometry = f'<Array as="points">{waypoints}</Array>' if waypoints else ""
    edges.append(
        f'<mxCell id="{_id()}" value={quoteattr(label)} style="{style}" edge="1" parent="1" '
        f'source="{source}" target="{target}"><mxGeometry relative="1" as="geometry">{geometry}'
        "</mxGeometry></mxCell>"
    )


def build() -> str:
    W, H = 2400, 1990
    backdrop.append(
        '<mxCell id="bg" value="" style="rounded=0;fillColor=#F8FAFC;strokeColor=none;" '
        f'vertex="1" parent="1"><mxGeometry x="0" y="0" width="{W}" height="{H}" as="geometry"/></mxCell>'
    )
    text(75, 24, 1800, 60, "NODO AI on Azure", 46, INK, True)
    text(75, 86, 2200, 30,
         "Meeting memory that stays private. Public entry points lead into isolated workloads, "
         "every model call passes one governed gateway, and the whole thing is Terraform, "
         "tested before it ever touches a subscription.", 17)

    w, h = 300, 145

    # =================================================================== 1. public edge
    band(50, 140, 2300, 235, "PUBLIC EDGE & IDENTITY")
    y = 190
    card(130, y, w, h, "identity/Users.svg", "Desktop and Android app",
         ["Local engine syncs finished", "meetings: PUT /v1/sync"], name="people")
    card(590, y, w, h, "networking/Front_Doors.svg", "Front Door Premium + WAF",
         ["Managed rules · bots · rate limit", "Custom domain · standby origin"], "safe", "afd")
    card(1050, y, w, h, "app_services/API_Management_Services.svg", "API Management",
         ["JWT check · rate limit · request id", "3 zones · second region"], "gateway", "apim", strong=True)
    card(1510, y, w, h, "other/Entra_Identity.svg", "Microsoft Entra ID",
         ["Sign-in with PKCE", "Tokens checked at the gateway"], "safe", "entra")
    card(1970, y, 330, h, "general/Resource_Groups.svg", "Shown: professional tier",
         ["basic tier: one region, no HA", "same module, one variable"], "plain", dashed=True)

    # =================================================================== 2. private network
    band(50, 400, 2300, 750, "PRIVATE NETWORK · 3 AVAILABILITY ZONES · NO PUBLIC ENDPOINTS ON THE DATA SERVICES", "#93C5FD", "#F3F8FF")
    r1, r2 = 520, 725
    card(130, r1, w, h, "other/Container_App_Environments.svg", "API",
         ["FastAPI · Container Apps", "3+ replicas · load shedding"], "plain", "api")
    card(130, r2, w, h, "integration/Service_Bus.svg", "Service Bus Premium",
         ["Ingest queue · scheduled polls", "Outbox · geo-DR alias"], "plain", "sb")
    card(620, r1, w, h, "app_services/API_Management_Services.svg", "AI gateway",
         ["Token budgets · region failover", "Only route to the models"], "ai", "aigw", strong=True)
    card(620, r2, w, h, "other/Worker_Container_App.svg", "Worker job",
         ["Transcribe · analyse · graph", "Retries · idempotent steps"], "plain", "worker")
    card(1090, r1, w, h, "ai_machine_learning/Azure_OpenAI.svg", "Azure OpenAI",
         ["Chat · extraction · embeddings", "Strict JSON schemas"], "ai", "oai")
    card(1090, r2, w, h, "ai_machine_learning/Speech_Services.svg", "Speech",
         ["Batch transcription · speakers", "Staged: job id, scheduled polls"], "ai", "speech")

    band(1440, 465, 880, 400, "PLATFORM", "#CBD5E1", "#FFFFFF", dashed=True)
    pw = 270
    px = (1465, 1755, 2045)
    card(px[0], r1, pw, h, "other/Container_App_Environments.svg", "Container Apps environment",
         ["VNet · zone-redundant", "NAT gateway for egress"], "plain")
    card(px[1], r1, pw, h, "containers/Container_Registries.svg", "Container Registry",
         ["Geo-replicated to standby", "Images scanned in CI"], "plain")
    card(px[2], r1, pw, h, "other/Private_Endpoints.svg", "Private endpoints",
         ["Vault · registry · storage", "Queue · search · models"], "plain")
    card(px[0], r2, pw, h, "general/Scheduler.svg", "Scheduled jobs",
         ["Retention · sweep · reindex", "Audit archive · migrate"], "plain", "jobs")
    card(px[1], r2, pw, h, "identity/Entra_Managed_Identities.svg", "Managed identities",
         ["One per workload", "Least-privilege DB roles"], "safe")
    card(px[2], r2, pw, h, "other/Container_App_Environments.svg", "Preflight checks",
         ["14 checks after deploy", "Smoke test through the gateway"], "plain")

    band(80, 910, 2240, 215, "DATA · PRIVATE ACCESS ONLY · ROW LEVEL SECURITY",
         "#FCD34D", "#FFFBEB", "datapanel")
    dw, dy = 395, 960
    dx = [105 + i * 440 for i in range(5)]
    card(dx[0], dy, dw, 140, "databases/Azure_Database_PostgreSQL_Server.svg", "PostgreSQL Flexible Server",
         ["Zone-redundant HA · delegated subnet", "Graph · vectors · answer cache"], "data", "pg")
    card(dx[1], dy, dw, 140, "storage/Storage_Accounts.svg", "Blob Storage",
         ["Audio under a key you own · GZRS", "Retention 7/30/90 d + lifecycle"], "data", "blob")
    card(dx[2], dy, dw, 140, "app_services/Search_Services.svg", "AI Search",
         ["Hybrid text + vector", "Owner filter on every query"], "data", "search")
    card(dx[3], dy, dw, 140, "security/Key_Vaults.svg", "Key Vault",
         ["Storage key · secrets", "Purge protection"], "safe", "kv")
    card(dx[4], dy, dw, 140, "storage/Storage_Accounts.svg", "Audit account",
         ["Immutable (WORM)", "Hash-chained hourly batches"], "data", "audit")

    # =================================================================== 3. standby region
    band(50, 1180, 2300, 230,
         "STANDBY REGION · TARGETS: DATABASE LOSS IN SECONDS, AUDIO IN MINUTES, BACK IN 30 TO 60 MINUTES · NOT YET REHEARSED",
         "#94A3B8", "#F8FAFC", dashed=True, name="standby")
    sw, sy = 345, 1235
    sx = [100 + i * 370 for i in range(6)]
    standby = [
        ("databases/Azure_Database_PostgreSQL_Server.svg", "PostgreSQL replica", ["Cross-region read replica", "Promotion is one switch"]),
        ("integration/Service_Bus.svg", "Service Bus", ["Geo-DR alias paired", "Queue follows the alias"]),
        ("storage/Storage_Accounts.svg", "Storage secondary", ["GZRS: audio and exports", "Audit account replicated"]),
        ("app_services/Search_Services.svg", "AI Search", ["Index rebuilt from the replica", "Search is derived data"]),
        ("other/Container_App_Environments.svg", "Container Apps", ["Own network, scaled to zero", "Started by the runbook"]),
        ("ai_machine_learning/Azure_OpenAI.svg", "OpenAI and Speech", ["Same models, stateless", "Gateway fails over to them"]),
    ]
    for x, (icon, title, lines) in zip(sx, standby, strict=True):
        card(x, sy, sw, 135, icon, title, lines, "standby", dashed=True)

    # =================================================================== 4. operations
    band(50, 1440, 2300, 235, "OPERATIONS, SECURITY & GOVERNANCE", "#CBD5E1", "#FFFFFF")
    ow, oh, oy = 290, 135, 1495
    ox = [90 + i * 320 for i in range(7)]
    ops = [
        ("devops/Application_Insights.svg", "Application Insights", ["OpenTelemetry · workbook", "Availability web tests"]),
        ("management_governance/Log_Analytics_Workspaces.svg", "Log Analytics", ["Diagnostics · activity log", "Ids only, no content"]),
        ("management_governance/Monitor.svg", "Monitor alerts", ["SLO burn-rate · fast, slow", "Index, sweep, audit alarms"]),
        ("security/Azure_Defender.svg", "Defender for Cloud", ["Plans per resource type", "Posture and threats"]),
        ("management_governance/Policy.svg", "Azure Policy", ["EU regions only", "Security benchmark"]),
        ("general/Cost_Budgets.svg", "Budget and locks", ["Cost alerts", "Delete lock on the data"]),
        ("devops/Azure_DevOps.svg", "Terraform · GitHub OIDC", ["Deploy without stored keys", "Plan tests, CI image scan"]),
    ]
    for x, (icon, title, lines) in zip(ox, ops, strict=True):
        card(x, oy, ow, oh, icon, title, lines, "ops")

    # =================================================================== 5. guarantees
    band(50, 1705, 2300, 240, "WHAT THE DESIGN GUARANTEES", "#CBD5E1", "#FFFFFF")
    gw, gh, gy = 345, 150, 1765
    gx = [100 + i * 370 for i in range(6)]
    proofs = [
        ("Isolated by design", ["Forced row level security, checked", "by preflight; owner filter on search"], GREEN),
        ("No long-lived credentials", ["A managed identity per workload,", "OIDC for deploys, secrets in Key Vault"], GREEN),
        ("Never trust the model", ["Every citation re-checked against", "the transcript; schemas are strict"], ORANGE),
        ("Provable audit trail", ["Append-only log, hash chain,", "write-once archive, GDPR export and erase"], PURPLE),
        ("Fails soft", ["Bulkheads, breakers, retries,", "load shedding, sources-only fallback"], BLUE),
        ("Tested offline", ["680 backend tests on real PostgreSQL,", "112 app tests, 9 Terraform plan tests"], DARK),
    ]
    for x, (big, lines, color) in zip(gx, proofs, strict=True):
        proof(x, gy, gw, gh, big, lines, color)

    text(75, 1955, 2250, 28,
         "Blue: public HTTPS  ·  Orange: governed model path  ·  Dark: private traffic  ·  "
         "Green dashed: identity check  ·  Purple dashed: scheduled jobs  ·  Grey dashed: replication",
         14)

    # =================================================================== arrows
    edge("people", "afd", BLUE)
    badge(560, 262, 1, BLUE)
    edge("afd", "apim", BLUE)
    badge(1020, 262, 2, BLUE)
    edge("apim", "entra", GREEN, dashed=True)
    text(1380, 226, 150, 24, "checks the token", 13, INK, False, "center")
    edge("apim", "api", BLUE, exit_xy=(0.5, 1), entry_xy=(0.5, 0), points=((1200, 345), (1200, 470), (280, 470)))
    badge(1200, 430, 3, BLUE)
    edge("api", "sb", DARK, exit_xy=(0.5, 1), entry_xy=(0.5, 0))
    badge(280, 695, 4, DARK)
    edge("sb", "worker", DARK)
    badge(525, 797, 5, DARK)
    edge("api", "aigw", ORANGE, label="questions")
    edge("worker", "aigw", ORANGE, exit_xy=(0.5, 0), entry_xy=(0.5, 1))
    badge(770, 695, 6, ORANGE)
    edge("aigw", "oai", ORANGE)
    edge("worker", "speech", ORANGE, label="submit and poll")
    edge("api", "datapanel", DARK, exit_xy=(0, 0.5), entry_xy=(0.02, 0),
         points=((105, 592), (105, 892), (125, 892)), label="queries")
    edge("worker", "datapanel", DARK, exit_xy=(0.5, 1), entry_xy=(0.3, 0), label="writes")
    edge("jobs", "audit", PURPLE, dashed=True, exit_xy=(0.5, 1), entry_xy=(0.5, 0),
         points=((1600, 892), (2062, 892)), label="archive, sweep")
    edge("datapanel", "standby", GREY, dashed=True, exit_xy=(0.5, 1), entry_xy=(0.5, 0),
         label="continuous replication")

    body = "".join(backdrop + edges + cells)
    return (
        '<mxfile host="app.diagrams.net"><diagram id="post" name="NODO AI on Azure">'
        f'<mxGraphModel dx="{W}" dy="{H}" grid="0" gridSize="10" guides="1" tooltips="1" connect="1" '
        f'arrows="1" fold="1" page="0" pageScale="1" pageWidth="{W}" pageHeight="{H}" math="0" shadow="0" '
        'background="#F8FAFC" defaultFontFamily="Segoe UI"><root><mxCell id="0"/>'
        f'<mxCell id="1" parent="0"/>{body}</root></mxGraphModel></diagram></mxfile>\n'
    )


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8", newline="\n")
    print(OUT)
