"""The outline and fragments API (FR-7): section counts, first locators, the outline version, the
fragments of one section (optionally with its subsections) and optional paging. Memory engine;
the text is invented."""

from __future__ import annotations

import pytest

from tests.source_contract import OWNER, fragment, section, source

WS = "workspace-a"
BOB = {"Authorization": "Bearer bob-token-0123456789"}


def _add(app, sections, fragments, source_id: str = "doc1") -> None:
    container = app.state.container
    container.sources.add(WS, source(source_id, owner=OWNER, minutes=20, ws=WS))
    container.fragments.add_version(WS, source_id, tuple(sections), tuple(fragments))


def _add_tree(app) -> None:
    """A DOCX-like outline: two H1 sections, the first with two H2 children."""
    _add(
        app,
        (
            section("h1a", "doc1", 0),
            section("h2a", "doc1", 0, parent="h1a", level=2),
            section("h2b", "doc1", 1, parent="h1a", level=2),
            section("h1b", "doc1", 1),
            section("empty", "doc1", 2),
        ),
        (
            fragment("f0", "doc1", 0, "Opening words", "h1a"),
            fragment("f1", "doc1", 1, "First detail", "h2a"),
            fragment("f2", "doc1", 2, "Second detail", "h2a"),
            fragment("f3", "doc1", 3, "Third detail", "h2b"),
            fragment("f4", "doc1", 4, "Closing words", "h1b"),
        ),
    )


def _ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["fragments"]]


def test_outline_reports_counts_first_locators_and_version(app, client, auth_headers) -> None:
    _add_tree(app)

    response = client.get("/v1/sources/doc1/outline", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    by_id = {item["id"]: item for item in body["sections"]}
    assert [item["id"] for item in body["sections"]] == ["h1a", "h2a", "h2b", "h1b", "empty"]
    assert {key: value["fragment_count"] for key, value in by_id.items()} == {
        "h1a": 1,
        "h2a": 2,
        "h2b": 1,
        "h1b": 1,
        "empty": 0,
    }
    assert by_id["h2a"]["first_locator"] == {"chars": [0, 12], "page": 2}
    assert by_id["h1b"]["first_locator"] == {"chars": [0, 13], "page": 5}
    assert by_id["empty"]["first_locator"] is None


def test_fragments_of_one_section_only(client, app, auth_headers) -> None:
    _add_tree(app)

    response = client.get("/v1/sources/doc1/fragments?section_id=h1a", headers=auth_headers)

    assert _ids(response) == ["f0"]
    assert response.json()["version"] == 1
    assert response.json()["next_offset"] is None


def test_fragments_with_subsections_follow_the_tree(client, app, auth_headers) -> None:
    _add_tree(app)

    whole = client.get(
        "/v1/sources/doc1/fragments",
        headers=auth_headers,
        params={"section_id": "h1a", "include_subsections": "true"},
    )
    leaf = client.get(
        "/v1/sources/doc1/fragments",
        headers=auth_headers,
        params={"section_id": "h2a", "include_subsections": "true"},
    )

    assert _ids(whole) == ["f0", "f1", "f2", "f3"]
    assert _ids(leaf) == ["f1", "f2"]


def test_unknown_section_is_404(client, app, auth_headers) -> None:
    _add_tree(app)
    _add(
        app,
        (section("other", "doc2", 0),),
        (fragment("g0", "doc2", 0, "Elsewhere", "other"),),
        "doc2",
    )

    for query in ("section_id=nope", "section_id=other"):
        response = client.get(f"/v1/sources/doc1/fragments?{query}", headers=auth_headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    meeting = client.get("/v1/sources/seed-m1/fragments?section_id=h1a", headers=auth_headers)
    assert meeting.status_code == 404
    too_long = client.get(f"/v1/sources/doc1/fragments?section_id={'x' * 65}", headers=auth_headers)
    assert too_long.status_code == 422


def test_paging_with_limit_offset_and_next_offset(client, app, auth_headers) -> None:
    _add_tree(app)
    url = "/v1/sources/doc1/fragments"

    first = client.get(url, headers=auth_headers, params={"limit": 2})
    second = client.get(url, headers=auth_headers, params={"limit": 2, "offset": 2})
    last = client.get(url, headers=auth_headers, params={"limit": 2, "offset": 4})
    exact = client.get(url, headers=auth_headers, params={"limit": 5})
    beyond = client.get(url, headers=auth_headers, params={"limit": 2, "offset": 9})
    in_section = client.get(
        url,
        headers=auth_headers,
        params={"section_id": "h1a", "include_subsections": "true", "limit": 3, "offset": 1},
    )

    assert _ids(first) == ["f0", "f1"] and first.json()["next_offset"] == 2
    assert _ids(second) == ["f2", "f3"] and second.json()["next_offset"] == 4
    assert _ids(last) == ["f4"] and last.json()["next_offset"] is None
    assert len(_ids(exact)) == 5 and exact.json()["next_offset"] is None
    assert _ids(beyond) == [] and beyond.json()["next_offset"] is None
    assert _ids(in_section) == ["f1", "f2", "f3"] and in_section.json()["next_offset"] is None
    assert "total" not in first.json()
    for params in ({"limit": 0}, {"limit": 501}, {"offset": -1}):
        assert client.get(url, headers=auth_headers, params=params).status_code == 422


def test_without_limit_a_long_document_still_returns_every_fragment(
    client, app, auth_headers
) -> None:
    _add(
        app,
        (section("only", "doc1", 0),),
        tuple(fragment(f"f{n}", "doc1", n, f"Line {n}", "only") for n in range(600)),
    )

    response = client.get("/v1/sources/doc1/fragments", headers=auth_headers)
    outline = client.get("/v1/sources/doc1/outline", headers=auth_headers).json()

    assert len(_ids(response)) == 600
    assert response.json()["next_offset"] is None
    assert outline["sections"][0]["fragment_count"] == 600


def test_without_new_parameters_the_answer_is_as_before_plus_null_next_offset(
    client, app, auth_headers
) -> None:
    _add_tree(app)

    body = client.get("/v1/sources/doc1/fragments", headers=auth_headers).json()

    assert set(body) == {"version", "fragments", "next_offset"}
    assert body["version"] == 1
    assert body["next_offset"] is None
    assert [item["id"] for item in body["fragments"]] == ["f0", "f1", "f2", "f3", "f4"]
    assert body["fragments"][0] == {
        "id": "f0",
        "section_id": "h1a",
        "ordinal": 0,
        "text": "Opening words",
        "locator": {"chars": [0, 13], "page": 1},
        "speaker_id": None,
        "confidence": 0.9,
    }


def test_meeting_outline_is_empty_with_the_transcript_version(client, auth_headers) -> None:
    outline = client.get("/v1/sources/seed-m1/outline", headers=auth_headers)
    fragments = client.get("/v1/sources/seed-m1/fragments", headers=auth_headers)

    assert outline.status_code == 200
    assert outline.json() == {"sections": [], "version": fragments.json()["version"]}
    assert outline.json()["version"] == 1


def test_other_user_gets_404(client, app, auth_headers) -> None:
    _add_tree(app)
    app.state.container.auth.add_dev_token("bob-token-0123456789", "user-b", WS)

    for path in (
        "/v1/sources/doc1/outline",
        "/v1/sources/doc1/fragments?section_id=h1a",
        "/v1/sources/doc1/fragments?limit=2",
    ):
        response = client.get(path, headers=BOB)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "path",
    (
        "/v1/sources/doc1/outline",
        "/v1/sources/doc1/fragments?section_id=h1a&include_subsections=true",
        "/v1/sources/doc1/fragments?limit=2&offset=1",
    ),
)
def test_azure_engine_answers_501(app, client, auth_headers, path) -> None:
    app.state.container.sources = None
    app.state.container.fragments = None

    response = client.get(path, headers=auth_headers)

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_available_on_engine"
