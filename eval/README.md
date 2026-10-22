# Golden-set evaluation

The fixture set exercises grounded answers across meetings, languages, projects, deletion state, and workspace boundaries. The data is fictional and stable so CI results can be compared over time.

Metrics follow the product blueprint:

- Citation precision: the share of returned citations that match the annotated supporting segments. The beta target is at least 95 percent.
- Answer faithfulness: the share of answer claims entailed by cited, non-deleted segments. The beta target is at least 95 percent.
- Cross-meeting fact accuracy: correctness on temporal changes and facts assembled across meetings. The beta target is at least 90 percent.
- ACL leak count: citations or answer content recovered from a workspace unavailable to the caller. The required value is 0.

Validate the static data with only the Python standard library:

```powershell
python eval/validate_golden_set.py
```

A future runner will load each JSONL case, choose the requested locale when `question` is localized, and call `POST /v1/query` with the case `workspace_id`, `caller_user_id`, and question. It will compare answerability and returned segment citations with the expected and forbidden lists. Deleted segments and segments from other workspaces must never be returned.
