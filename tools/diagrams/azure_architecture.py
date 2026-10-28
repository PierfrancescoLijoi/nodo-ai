"""Draws docs/images/azure-architecture-layered.svg, the layered picture of the Azure architecture.

    python tools/diagrams/azure_architecture.py

The numbers on it come from docs/AZURE-ARCHITECTURE.md; change the document and this together.
Render a PNG with any browser (Edge: msedge --headless --screenshot=out.png --window-size=1800,1450 file.svg).
"""

from __future__ import annotations

from html import escape
from pathlib import Path

W, H = 1800, 1450
OUT = Path(__file__).resolve().parents[2] / "docs" / "images" / "azure-architecture-layered.svg"

EDGE, ID, COMPUTE, BUS, DATA, SEARCH, BLOB, KEY, AI = (
    "#6D4AFF", "#D6336C", "#0078D4", "#E76500", "#3B5BDB", "#0F766E", "#2F9E44", "#6B7280", "#00A3A3",
)  # fmt: skip

parts: list[str] = []


def add(svg: str) -> None:
    parts.append(svg)


def text(x, y, value, cls="", anchor="start", extra=""):
    add(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}" {extra}>{escape(value)}</text>')


def icon(x, y, color, glyph):
    add(f'<rect x="{x}" y="{y}" width="52" height="52" rx="12" fill="{color}"/>')
    size = 22 if len(glyph) <= 2 else 17
    text(x + 26, y + 33, glyph, "white", "middle", f'font-size="{size}" font-weight="800"')


def card(x, y, w, h, title, lines, color, glyph, title_size=20):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" class="card"/>')
    icon(x + 16, y + 16, color, glyph)
    text(x + 80, y + 40, title, "cardTitle", extra=f'font-size="{title_size}"')
    for i, line in enumerate(lines):
        text(x + 18 if i > 0 else x + 80, y + 66 + i * 21 if i > 0 else y + 62, line, "cardText" if i == 0 else "small")


def band(x, y, w, label, fill="url(#navy)", height=46, extra_right=""):
    add(f'<path d="M{x} {y + height}V{y + 14}a14 14 0 0 1 14-14H{x + w - 14}a14 14 0 0 1 14 14V{y + height}Z" fill="{fill}"/>')
    text(x + 24, y + 30, label, "section white")
    if extra_right:
        text(x + w - 24, y + 29, extra_right, "tiny white", "end")


def panel(x, y, w, h, stroke="#B8C7D6", fill="#FFFFFF", dash=""):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{fill}" stroke="{stroke}" stroke-width="1.8" {dash}/>')


def arrow(points, kind="blue"):
    d = "M" + " L".join(f"{x} {y}" for x, y in points)
    add(f'<path d="{d}" class="{kind}Flow"/>')


# ----------------------------------------------------------------------------------------- frame
add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">')
add('<title id="title">NODO AI layered Azure architecture</title>')
add('<desc id="desc">Public edge and identity, a mandatory API gateway, private zone-redundant compute, a mandatory AI gateway to the models, private data services, a warm standby region, and cross-cutting operations.</desc>')
add("""<defs>
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#F7FAFC"/><stop offset="1" stop-color="#EEF3F8"/></linearGradient>
<linearGradient id="navy" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#0B2239"/><stop offset="1" stop-color="#183A59"/></linearGradient>
<linearGradient id="orange" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#FF9900"/><stop offset="1" stop-color="#E76500"/></linearGradient>
<linearGradient id="steel" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#1F5C8E"/><stop offset="1" stop-color="#2F7CB8"/></linearGradient>
<filter id="softShadow" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#0B2239" flood-opacity=".09"/></filter>
<marker id="aBlue" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#2563EB"/></marker>
<marker id="aOrange" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#E76500"/></marker>
<marker id="aTeal" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#0F766E"/></marker>
<style>
text { font-family: Inter, "Segoe UI", Arial, sans-serif; fill: #10243A; }
.section { font-size: 17px; font-weight: 800; letter-spacing: 1.4px; }
.cardTitle { font-size: 20px; font-weight: 750; }
.cardText { font-size: 14px; fill: #40566D; }
.small { font-size: 12.5px; fill: #52687E; }
.tiny { font-size: 11.5px; fill: #60758A; }
.white { fill: #FFFFFF; }
.chip { font-size: 15px; font-weight: 700; }
.blueFlow { fill: none; stroke: #2563EB; stroke-width: 4; marker-end: url(#aBlue); }
.orangeFlow { fill: none; stroke: #E76500; stroke-width: 5; marker-end: url(#aOrange); }
.tealFlow { fill: none; stroke: #0F766E; stroke-width: 3; stroke-dasharray: 9 7; marker-end: url(#aTeal); }
.card { fill: #FFFFFF; stroke: #B8C7D6; stroke-width: 1.5; filter: url(#softShadow); }
.mini { fill: #F2F7FC; stroke: #C5D3E2; stroke-width: 1.2; }
</style>
</defs>""")
add(f'<rect width="{W}" height="{H}" fill="url(#bg)"/>')

text(48, 58, "NODO AI on Azure", "", extra='font-size="38" font-weight="800"')
text(48, 91, "Layered architecture · the public edge and the API gateway in front, the AI gateway in front of the models, every data service private, a warm standby region behind", "", extra='font-size="16" fill="#536A80"')
add('<rect x="1496" y="36" width="256" height="48" rx="12" fill="#FFFFFF" stroke="#D4DEE8"/>')
add('<rect x="1510" y="47" width="48" height="27" rx="6" fill="#0078D4"/>')
text(1534, 67, "Az", "white", "middle", 'font-size="15" font-weight="800"')
text(1570, 66, "EU · primary + standby", "", extra='font-size="15" font-weight="700"')

# ------------------------------------------------------------------------------------ 01 · edge
band(44, 118, 1712, "01 · PUBLIC EDGE, IDENTITY & API GATEWAY")
panel(44, 164, 1712, 176)
cards01 = [
    ("People & apps", ["Android · desktop · web", "Sign in with PKCE"], "#334155", "U"),
    ("Entra External ID", ["Sign-up and sign-in", "Short-lived access token"], ID, "ID"),
    ("Front Door Premium + WAF", ["TLS · managed rules · bot rules", "Rate limit · regional failover"], EDGE, "FD"),
    ("API Management", ["Token check · rate limit per user", "Circuit breaker · region backend"], EDGE, "API"),
]
x = 76
for title, lines, color, glyph in cards01:
    card(x, 192, 380, 116, title, lines, color, glyph, 19 if len(title) > 20 else 20)
    x += 380 + 42.67
for i in range(3):
    ax = 76 + 380 + i * (380 + 42.67)
    arrow([(ax + 4, 250), (ax + 38, 250)])
text(1500, 330, "same-origin API path only: /v1/* and /readyz", "tiny", "end")

# --------------------------------------------------------------------------------------- the VNet
panel(44, 372, 1712, 700, stroke="#6EA3F5", fill="#EDF4FE")
add('<rect x="68" y="388" width="1664" height="40" rx="10" fill="#E3EEFD" stroke="#9DBFF2" stroke-dasharray="6 5"/>')
text(900, 415, "AZURE VNET · PRIVATE SUBNETS · THREE AVAILABILITY ZONES · PRIVATE ENDPOINTS · NO PUBLIC DATA ENDPOINT", "section", "middle", 'font-size="15"')
arrow([(1566, 308), (1566, 350), (1566, 372), (1566, 452)])  # APIM into the private compute

text(76, 470, "02 · PRIVATE COMPUTE  (CONTAINER APPS · INTERNAL · WORKER SCALES ON THE QUEUE)", "section", extra='font-size="16"')
card(76, 486, 300, 176, "API", ["Container Apps · 3+ replicas", "· one per zone · internal only", "· load shedding · idempotency", "· sources only if the model is down"], COMPUTE, "CA")
card(420, 486, 290, 176, "Service Bus", ["Premium · private · alias", "· queue and scheduled polls", "· duplicate detection · dead letters", "· 5 deliveries, then an alert"], BUS, "SB")
# worker block
add('<rect x="756" y="486" width="560" height="176" rx="14" class="card"/>')
add('<path d="M756 520V500a14 14 0 0 1 14-14H1302a14 14 0 0 1 14 14V520Z" fill="url(#navy)"/>')
text(776, 510, "WORKER JOBS · KEDA ON THE QUEUE", "section white", extra='font-size="15"')
for i, (t1, t2, t3) in enumerate(
    [("Speech in stages", "submit · poll · collect", "never waits for the provider"),
     ("Index & extract", "embed · graph · entities", "strict model schemas"),
     ("Exports", "one JSON per person", "GDPR 15 and 20")]
):  # fmt: skip
    bx = 774 + i * 178
    add(f'<rect x="{bx}" y="534" width="166" height="96" rx="10" class="mini"/>')
    text(bx + 83, 562, t1, "", "middle", 'font-size="14.5" font-weight="750"')
    text(bx + 83, 586, t2, "small", "middle")
    text(bx + 83, 606, t3, "tiny", "middle")
text(1036, 652, "stateless · idempotent at every stage · bounded retries", "tiny", "middle")
# scheduled jobs
add('<rect x="1356" y="486" width="368" height="176" rx="14" class="card"/>')
add('<path d="M1356 520V500a14 14 0 0 1 14-14H1710a14 14 0 0 1 14 14V520Z" fill="#475569"/>')
text(1376, 510, "SCHEDULED & MANUAL JOBS", "section white", extra='font-size="15"')
for i, (a, b) in enumerate(
    [("retention", "audio erased by policy · every 6 h"), ("sweep", "lost work restarted · every 30 min"), ("audit archive", "hash-chained files · hourly"), ("index check", "search and graph vs database · weekly")]
):  # fmt: skip
    text(1376, 550 + i * 24, a, "", extra='font-size="13.5" font-weight="750"')
    text(1486, 550 + i * 24, b, "small")
text(1376, 648, "manual: preflight · migrate · reindex · calibrate", "small")
arrow([(380, 574), (416, 574)])
arrow([(714, 574), (752, 574)], "teal")

# ------------------------------------------------------------------------------------- 04 · data
panel(76, 716, 934, 340, stroke="#9EB1C5", fill="#FFFFFF")
text(96, 745, "04 · DATA · PRIVATE ENDPOINTS ONLY", "section", extra='font-size="15"')
data = [
    ("Audio blobs", ["GZRS · CMK", "write-only link", "malware scan"], BLOB, "BL"),
    ("PostgreSQL", ["zone-redundant HA", "row-level security", "replica · 35 d restore"], DATA, "PG"),
    ("AI Search", ["3 replicas", "4 analyzers + vectors", "rebuilt from PostgreSQL"], SEARCH, "AS"),
    ("Audit log", ["private WORM account", "hash-chained files", "kept before any change"], BLOB, "WO"),
    ("Key Vault", ["Premium · purge guard", "audio key rotates", "secrets by reference"], KEY, "KV"),
]
x = 90
for title, lines, color, glyph in data:
    add(f'<rect x="{x}" y="760" width="168" height="182" rx="14" class="card"/>')
    icon(x + 12, 772, color, glyph)
    text(x + 72, 806, title, "", extra='font-size="16.5" font-weight="750"')
    for i, line in enumerate(lines):
        text(x + 14, 846 + i * 24, line, "cardText" if i == 0 else "small", extra='font-size="13.5"' if i == 0 else "")
    x += 168 + 16
add('<rect x="90" y="956" width="894" height="90" rx="12" fill="#F5F9FD" stroke="#C5D3E2"/>')
text(110, 982, "Azure Speech", "", extra='font-size="17" font-weight="750"')
text(110, 1004, "batch transcription · diarization · four languages", "small")
text(110, 1026, "called by the worker over a private endpoint", "tiny")
text(560, 982, "Only PostgreSQL and audio are irreplaceable.", "", extra='font-size="14" font-weight="700"')
text(560, 1004, "The search index and the entity vectors are derived and rebuilt;", "small")
text(560, 1026, "a weekly check finds drift and the reindex job repairs it.", "small")

# --------------------------------------------------------------------------------- 03 · AI gateway
add('<rect x="1050" y="716" width="674" height="340" rx="16" fill="#FFF3E0" stroke="#E76500" stroke-width="2.4"/>')
add('<path d="M1050 764V730a14 14 0 0 1 14-14H1710a14 14 0 0 1 14 14V764Z" fill="url(#orange)"/>')
text(1072, 747, "03 · AI GATEWAY · THE ONLY PATH TO THE MODELS", "section white", extra='font-size="16"')
chips = ["Entra token check", "Token budget per caller", "Managed identity to models", "Retry in the other region", "Circuit breaker per backend", "Private endpoints, no keys"]
for i, label in enumerate(chips):
    cx, cy = 1070 + (i % 3) * 214, 784 + (i // 3) * 66
    add(f'<rect x="{cx}" y="{cy}" width="204" height="54" rx="10" fill="#FFFFFF" stroke="#F1C089"/>')
    text(cx + 102, cy + 33, label, "chip", "middle", 'font-size="12.5"')
arrow([(1387, 920), (1387, 944)], "orange")
add('<rect x="1070" y="948" width="634" height="92" rx="14" class="card"/>')
icon(1086, 968, AI, "AO")
text(1152, 992, "Azure OpenAI", "cardTitle")
text(1152, 1014, "chat and embeddings · EU data zone · two regions", "cardText")
text(1152, 1033, "callers: the API (questions) and the worker (extraction)", "small")

# --------------------------------------------------------------------------------------- flows
arrow([(150, 662), (150, 714)])  # API to data
arrow([(900, 662), (900, 714)])  # worker to data
arrow([(300, 662), (300, 684), (1387, 684), (1387, 716)], "orange")  # API to AI gateway
arrow([(1200, 662), (1200, 716)], "orange")  # worker to AI gateway
arrow([(110, 308), (110, 340), (60, 340), (60, 830), (88, 830)], "teal")  # direct audio upload
text(72, 362, "direct audio upload · write-only link", "tiny")

# ------------------------------------------------------------------------------ 05 · standby
band(44, 1090, 1712, "05 · STANDBY REGION · WARM DATA, COLD COMPUTE", fill="url(#steel)", extra_right="recovery point: seconds (database) · minutes (audio) · recovery time target: 30 to 60 minutes")
panel(44, 1136, 1712, 112, stroke="#9EB1C5")
standby = [
    ("PostgreSQL replica", "cross-region, promoted by hand"),
    ("Storage GZRS", "audio and audit copied"),
    ("Queue alias", "Service Bus geo-DR"),
    ("Models + Speech", "second accounts, retried by the gateway"),
    ("Search", "rebuilt by the reindex job"),
    ("API at zero", "one switch scales it up: dr_active"),
]
x = 62
for title, line in standby:
    add(f'<rect x="{x}" y="1154" width="262" height="78" rx="12" class="card"/>')
    text(x + 16, 1184, title, "", extra='font-size="16.5" font-weight="750"')
    text(x + 16, 1208, line, "small")
    x += 262 + 22.4

# ----------------------------------------------------------------------------- cross-cutting
band(44, 1278, 1712, "CROSS-CUTTING OPERATIONS, SECURITY & DELIVERY")
panel(44, 1324, 1712, 80, stroke="#C5D3E2")
cross = [
    ("Log Analytics · App Insights", "SLO burn-rate alerts · availability test"),
    ("Managed identities · Entra RBAC", "no keys · no shared storage key"),
    ("Azure Policy · Defender · locks", "EU regions only · malware scan"),
    ("Terraform · ACR · GitHub OIDC", "basic and professional tiers · tests in CI"),
    ("Preflight · smoke test · runbooks", "first-connection checks · rehearsed recovery"),
]
x = 70
for i, (a, b) in enumerate(cross):
    text(x, 1356, a, "", extra='font-size="15.5" font-weight="750"')
    text(x, 1380, b, "small")
    if i:
        add(f'<line x1="{x - 18}" y1="1340" x2="{x - 18}" y2="1392" stroke="#D5DEE8"/>')
    x += 340

# ---------------------------------------------------------------------------------------- legend
arrow([(60, 1430), (110, 1430)])
text(120, 1435, "application flow", "tiny")
arrow([(300, 1430), (350, 1430)], "orange")
text(360, 1435, "governed path to the models", "tiny")
arrow([(600, 1430), (650, 1430)], "teal")
text(660, 1435, "background work and direct upload", "tiny")
text(1750, 1435, "numbers: docs/AZURE-ARCHITECTURE.md · regenerate: tools/diagrams/azure_architecture.py", "tiny", "end")

add("</svg>")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(parts), encoding="utf-8")
print(OUT)
