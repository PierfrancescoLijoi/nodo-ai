"""The API Management policies, rendered and read the way the service will read them.

API Management validates policy XML when it is applied; a typo there is an apply that fails after an
hour of provisioning. These checks catch what can be caught first: the template renders, the XML is
well formed, only policy elements the service knows are used, and every {{named value}} the policy
reads is one Terraform defines."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[3] / "infra" / "terraform" / "modules" / "nodo"
POLICIES = MODULE / "policies"

# Elements of the API Management policy language that these templates may use (per section).
KNOWN = {
    "policies", "inbound", "backend", "outbound", "on-error", "base", "check-header", "value",
    "validate-jwt", "openid-config", "audiences", "audience", "issuers", "issuer",
    "rate-limit-by-key", "set-header", "set-backend-service", "forward-request",
    "validate-azure-ad-token", "client-application-ids", "application-id", "llm-token-limit",
    "authentication-managed-identity", "retry", "choose", "when", "otherwise",
}  # fmt: skip

CALLERS = ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]


def render(source: str, scope: dict) -> str:
    """The small part of the Terraform template language these files use: ${name}, and the line
    directives for / if / else / endif / endfor."""
    lines = source.splitlines()

    def directive(line: str) -> list[str] | None:
        text = line.strip()
        if not text.startswith("%{"):
            return None
        return text[2:].removesuffix("}").strip().removesuffix("~").strip().split()

    def block(index: int, stop: set[str]):
        nodes: list[tuple] = []
        while index < len(lines):
            words = directive(lines[index])
            if words is None:
                nodes.append(("text", lines[index]))
                index += 1
            elif words[0] in stop:
                return nodes, index
            elif words[0] == "for":
                inner, end = block(index + 1, {"endfor"})
                nodes.append(("for", words[1], words[3], inner))
                index = end + 1
            elif words[0] == "if":
                then, end = block(index + 1, {"else", "endif"})
                other: list[tuple] = []
                if directive(lines[end])[0] == "else":
                    other, end = block(end + 1, {"endif"})
                nodes.append(("if", words[1], then, other))
                index = end + 1
            else:
                raise AssertionError(f"unknown directive {lines[index]!r}")
        return nodes, index

    def emit(nodes: list[tuple], where: dict) -> list[str]:
        out: list[str] = []
        for node in nodes:
            if node[0] == "text":
                out.append(re.sub(r"\$\{(\w+)\}", lambda m: str(where[m.group(1)]), node[1]))
            elif node[0] == "for":
                for item in where[node[2]]:
                    out += emit(node[3], {**where, node[1]: item})
            else:
                out += emit(node[2] if where[node[1]] else node[3], where)
        return out

    tree, _ = block(0, set())
    return chr(10).join(emit(tree, scope))


def values(**over) -> dict:
    base = {
        "calls_per_minute": 300,
        "primary_region_display": "Sweden Central",
        "tokens_per_minute": 400_000,
        "token_limit_enabled": True,
        "has_secondary": True,
        "caller_client_ids": CALLERS,
    }
    return {**base, **over}


def parse(name: str, **over) -> ET.Element:
    text = render((POLICIES / name).read_text(encoding="utf-8"), values(**over))
    return ET.fromstring(text)


def names_defined_in_terraform() -> set[str]:
    gateway = (MODULE / "gateway.tf").read_text(encoding="utf-8")
    block = gateway[gateway.index("gateway_values = ") :]
    block = block[: block.index("} : {}")]
    return set(re.findall(r'"([a-z\-]+)"\s*=', block))


@pytest.mark.parametrize(
    "name", ["api.xml.tftpl", "ready.xml.tftpl", "ai.xml.tftpl", "web.xml.tftpl"]
)
@pytest.mark.parametrize("secondary", [True, False])
@pytest.mark.parametrize("limit", [True, False])
def test_every_template_renders_to_well_formed_xml_with_only_known_elements(
    name, secondary, limit
) -> None:
    root = parse(name, has_secondary=secondary, token_limit_enabled=limit)
    assert root.tag == "policies"
    assert {child.tag for child in root} <= {"inbound", "backend", "outbound", "on-error"}
    unknown = {element.tag for element in root.iter()} - KNOWN
    assert not unknown, f"{name}: elements the policy language does not have: {unknown}"


def test_every_named_value_a_policy_reads_is_defined_in_terraform() -> None:
    defined = names_defined_in_terraform()
    assert {"tenant-id", "oidc-issuer", "oidc-audience", "front-door-id"} <= defined
    for name in ("api.xml.tftpl", "ready.xml.tftpl", "ai.xml.tftpl", "web.xml.tftpl"):
        used = set(re.findall(r"\{\{([a-z\-]+)\}\}", (POLICIES / name).read_text(encoding="utf-8")))
        assert used <= defined, f"{name} reads {used - defined}"


def test_the_public_api_policy_checks_the_edge_then_the_token_then_the_rate() -> None:
    inbound = [e.tag for e in parse("api.xml.tftpl").find("inbound")]
    assert inbound.index("check-header") < inbound.index("validate-jwt")
    assert inbound.index("validate-jwt") < inbound.index("rate-limit-by-key")


def test_the_readiness_probe_inherits_nothing_so_it_needs_no_token() -> None:
    root = parse("ready.xml.tftpl")
    assert all(element.tag != "base" for element in root.iter())
    assert all(element.tag != "validate-jwt" for element in root.iter())


def test_the_ai_gateway_admits_only_the_named_callers_and_signs_in_as_itself() -> None:
    inbound = parse("ai.xml.tftpl").find("inbound")
    ids = [a.text for a in inbound.iter("application-id")]
    assert ids == CALLERS
    assert inbound.find("authentication-managed-identity") is not None
    assert [e.tag for e in inbound].index("validate-azure-ad-token") < [
        e.tag for e in inbound
    ].index("authentication-managed-identity")


def test_the_token_budget_can_be_switched_off_and_everything_else_stays() -> None:
    on = {e.tag for e in parse("ai.xml.tftpl", token_limit_enabled=True).iter()}
    off = {e.tag for e in parse("ai.xml.tftpl", token_limit_enabled=False).iter()}
    assert "llm-token-limit" in on and "llm-token-limit" not in off
    assert on - {"llm-token-limit"} == off


def test_the_retry_to_the_other_region_exists_only_when_there_is_another_region() -> None:
    two = parse("ai.xml.tftpl", has_secondary=True).find("backend")
    one = parse("ai.xml.tftpl", has_secondary=False).find("backend")
    assert two.find("retry") is not None and one.find("retry") is None
    assert one.find("forward-request") is not None
    retry = two.find("retry")
    assert retry.get("count") == "2" and retry.find("forward-request") is not None


def test_expressions_that_contain_double_quotes_sit_in_single_quoted_attributes() -> None:
    for name in ("api.xml.tftpl", "ready.xml.tftpl", "ai.xml.tftpl", "web.xml.tftpl"):
        text = (POLICIES / name).read_text(encoding="utf-8")
        for match in re.finditer(r'(\w[\w-]*)="(@\([^"]*"[^"]*\))"', text):
            pytest.fail(f"{name}: attribute {match.group(1)} holds quotes inside double quotes")


def test_the_web_pages_need_no_token_but_only_front_door_may_ask_for_them() -> None:
    inbound = [e.tag for e in parse("web.xml.tftpl").find("inbound")]
    assert "check-header" in inbound and "validate-jwt" not in inbound
    assert inbound.index("check-header") < inbound.index("rate-limit-by-key")
    assert inbound.index("rate-limit-by-key") < inbound.index("set-backend-service")


def test_the_web_policy_names_the_backends_terraform_defines() -> None:
    text = render((POLICIES / "web.xml.tftpl").read_text(encoding="utf-8"), values())
    used = set(re.findall(r'"(web-[a-z]+)"', text))
    gateway = (MODULE / "gateway.tf").read_text(encoding="utf-8")
    assert used == {"web-primary", "web-dr"}
    for backend in used:
        assert f'"{backend}"' in gateway, f"{backend} is not a backend in gateway.tf"


def test_the_edge_sends_web_paths_under_the_prefix_the_gateway_serves() -> None:
    edge = (MODULE / "edge.tf").read_text(encoding="utf-8")
    gateway = (MODULE / "gateway.tf").read_text(encoding="utf-8")
    destination = re.search(r'destination\s*=\s*"(/[a-z]+/)"', edge)
    api_path = re.search(r'name\s*=\s*"web"[\s\S]*?path\s*=\s*"([a-z]+)"', gateway)
    assert destination and api_path
    assert destination.group(1) == f"/{api_path.group(1)}/"
