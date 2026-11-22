from nodo_api.domain.graph import Entity, normalize_label
from nodo_api.domain.hierarchy import ParentSuggestion
from nodo_api.domain.models import Locale
from nodo_api.domain.sources import Fragment


class KeywordParentSuggester:
    def suggest(
        self,
        concept: Entity,
        candidates: tuple[Entity, ...],
        fragments: tuple[Fragment, ...],
        locale: Locale,
    ) -> tuple[ParentSuggestion, ...]:
        del locale
        concept_label = normalize_label(concept.label)
        result = []
        for candidate in candidates:
            candidate_label = normalize_label(candidate.label)
            evidence = tuple(
                fragment.id
                for fragment in fragments
                if concept_label in normalize_label(fragment.text)
                and candidate_label in normalize_label(fragment.text)
            )
            if evidence:
                result.append(ParentSuggestion(candidate.id, 0.5, evidence))
        return tuple(result)
