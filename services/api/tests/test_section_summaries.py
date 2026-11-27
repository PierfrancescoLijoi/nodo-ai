from __future__ import annotations

from nodo_api.domain.models import Locale
from nodo_api.domain.sources import Fragment, Locator
from nodo_api.resilience import DependencyUnavailable
from nodo_api.services.section_summaries import SectionSummaryService


class Store:
    def __init__(self):
        self.value = None

    def get(self, *args):
        return self.value

    def put(self, workspace_id, summary):
        self.value = summary


class Summarizer:
    model_version = "test-model"

    def __init__(self, answer="A concise summary."):
        self.answer = answer
        self.calls = 0

    def summarize(self, locale, fragments):
        self.calls += 1
        return self.answer


def _fragment():
    return Fragment("f1", "source", "section", 0, "untrusted text", Locator(chars=(0, 14)), 3)


def test_summary_is_cached_by_current_source_version():
    store, model = Store(), Summarizer()
    service = SectionSummaryService(store, model)

    first = service.get_or_generate("ws", "section", (_fragment(),), Locale.EN)
    second = service.get_or_generate("ws", "section", (_fragment(),), Locale.EN)

    assert first == second
    assert first and first.source_version == 3 and first.prompt_version == "section-summary-v1"
    assert model.calls == 1


def test_model_failure_is_absence_not_a_query_failure():
    class Down(Summarizer):
        def summarize(self, locale, fragments):
            raise DependencyUnavailable("summary-model")

    assert (
        SectionSummaryService(Store(), Down()).get_or_generate(
            "ws", "section", (_fragment(),), Locale.EN
        )
        is None
    )
