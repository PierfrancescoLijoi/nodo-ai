from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
FIXTURES_PATH = ROOT / "golden_set" / "fixtures" / "meetings.json"
CASES_PATH = ROOT / "golden_set" / "cases.jsonl"
SCHEMA_PATH = ROOT / "golden_set" / "schema.json"

CATEGORIES = {
    "single_meeting_fact",
    "decision_change_over_time",
    "speaker_ambiguity",
    "multilingual_cross_lingual",
    "incomplete_reference",
    "unanswerable",
    "entity_collision",
    "false_cross_project_join",
    "acl_negative",
    "deleted_data",
}
LOCALES = {"en", "pl", "it", "da"}
ANSWERABILITY = {"supported", "partial", "unanswerable"}
CASE_FIELDS = {
    "id",
    "category",
    "workspace_id",
    "caller_user_id",
    "question",
    "expected_answerability",
    "expected_citation_segment_ids",
    "forbidden_segment_ids",
    "notes",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"cases.jsonl line {line_number}: {error}") from error
            require(isinstance(value, dict), f"case line {line_number} must be an object")
            cases.append(value)
    return cases


def validate_fixtures(data: Any) -> tuple[dict[str, dict[str, Any]], set[str]]:
    require(isinstance(data, dict) and set(data) == {"meetings"}, "fixtures must contain only meetings")
    meetings = data["meetings"]
    require(isinstance(meetings, list) and len(meetings) == 6, "fixtures must contain exactly 6 meetings")
    meeting_ids: set[str] = set()
    segments: dict[str, dict[str, Any]] = {}
    languages = Counter()
    workspaces: set[str] = set()
    required_meeting = {"id", "workspace_id", "project_id", "title", "spoken_language", "started_at", "segments"}
    required_segment = {"id", "speaker", "start_ms", "end_ms", "version", "text"}

    for meeting in meetings:
        require(isinstance(meeting, dict), "each meeting must be an object")
        require(required_meeting <= set(meeting), f"meeting missing fields: {meeting}")
        meeting_id = meeting["id"]
        require(isinstance(meeting_id, str) and meeting_id, "meeting id must be non-empty")
        require(meeting_id not in meeting_ids, f"duplicate meeting id: {meeting_id}")
        meeting_ids.add(meeting_id)
        require(meeting["spoken_language"] in LOCALES, f"invalid language in {meeting_id}")
        languages[meeting["spoken_language"]] += 1
        workspaces.add(meeting["workspace_id"])
        require(isinstance(meeting["segments"], list) and meeting["segments"], f"{meeting_id} has no segments")
        for segment in meeting["segments"]:
            require(isinstance(segment, dict) and required_segment <= set(segment), f"invalid segment in {meeting_id}")
            segment_id = segment["id"]
            require(segment_id not in segments, f"duplicate segment id: {segment_id}")
            require(isinstance(segment["start_ms"], int) and segment["start_ms"] >= 0, f"invalid start in {segment_id}")
            require(isinstance(segment["end_ms"], int) and segment["end_ms"] >= segment["start_ms"], f"invalid end in {segment_id}")
            require(isinstance(segment["version"], int) and segment["version"] >= 1, f"invalid version in {segment_id}")
            require(bool(segment["speaker"]) and bool(segment["text"]), f"empty text field in {segment_id}")
            if segment.get("tombstoned"):
                require(bool(segment.get("tombstoned_at")), f"tombstone timestamp missing in {segment_id}")
            segments[segment_id] = {**segment, "workspace_id": meeting["workspace_id"]}

    require(languages == Counter({"en": 2, "it": 2, "pl": 1, "da": 1}), f"unexpected language balance: {languages}")
    require(len(workspaces) >= 2, "fixtures need at least two workspaces")
    return segments, workspaces


def validate_cases(cases: list[dict[str, Any]], segments: dict[str, dict[str, Any]], workspaces: set[str]) -> None:
    require(len(cases) >= 60, "at least 60 cases are required")
    ids: set[str] = set()
    categories = Counter()
    localized_count = 0

    for case in cases:
        case_id = case.get("id", "<missing>")
        require(set(case) == CASE_FIELDS, f"{case_id}: fields do not match the case schema")
        require(isinstance(case_id, str) and case_id and case_id not in ids, f"duplicate or empty case id: {case_id}")
        ids.add(case_id)
        require(case["category"] in CATEGORIES, f"{case_id}: invalid category")
        categories[case["category"]] += 1
        require(case["workspace_id"] in workspaces, f"{case_id}: unknown workspace")
        require(isinstance(case["caller_user_id"], str) and case["caller_user_id"], f"{case_id}: invalid caller")
        require(case["expected_answerability"] in ANSWERABILITY, f"{case_id}: invalid answerability")
        question = case["question"]
        if isinstance(question, dict):
            require(set(question) == LOCALES, f"{case_id}: localized question must contain en, pl, it, da")
            require(all(isinstance(value, str) and value.strip() for value in question.values()), f"{case_id}: empty translation")
            localized_count += 1
        else:
            require(isinstance(question, str) and question.strip(), f"{case_id}: invalid question")
        for field in ("expected_citation_segment_ids", "forbidden_segment_ids"):
            references = case[field]
            require(isinstance(references, list) and len(references) == len(set(references)), f"{case_id}: invalid {field}")
            missing = set(references) - set(segments)
            require(not missing, f"{case_id}: unknown segment ids in {field}: {sorted(missing)}")
        expected = set(case["expected_citation_segment_ids"])
        forbidden = set(case["forbidden_segment_ids"])
        require(not expected & forbidden, f"{case_id}: a segment cannot be expected and forbidden")
        if case["expected_answerability"] == "unanswerable":
            require(not expected, f"{case_id}: unanswerable cases cannot require citations")
        if case["category"] == "acl_negative":
            require(case["expected_answerability"] == "unanswerable", f"{case_id}: ACL negatives must be unanswerable")
            require(not expected, f"{case_id}: ACL negatives must have zero expected citations")
            require(bool(forbidden), f"{case_id}: ACL negatives must identify forbidden segments")
            require(all(segments[item]["workspace_id"] != case["workspace_id"] for item in forbidden), f"{case_id}: ACL forbidden segments must be from another workspace")
        if case["category"] == "deleted_data":
            require(bool(forbidden), f"{case_id}: deleted-data cases need forbidden segments")
            require(all(segments[item].get("tombstoned") is True for item in forbidden), f"{case_id}: deleted-data forbidden segments must be tombstoned")

    require(set(categories) == CATEGORIES, f"missing categories: {sorted(CATEGORIES - set(categories))}")
    require(max(categories.values()) - min(categories.values()) <= 1, f"categories are not balanced: {categories}")
    require(localized_count >= 20, f"at least 20 localized questions are required, found {localized_count}")


def main() -> int:
    schema = load_json(SCHEMA_PATH)
    require(isinstance(schema, dict) and "$defs" in schema, "schema.json must define reusable schemas")
    segments, workspaces = validate_fixtures(load_json(FIXTURES_PATH))
    cases = load_cases(CASES_PATH)
    validate_cases(cases, segments, workspaces)
    print(f"PASS: 6 meetings, {len(segments)} segments, {len(cases)} cases, 10 balanced categories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

