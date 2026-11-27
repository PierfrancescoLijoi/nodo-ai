import pytest

from nodo_api.adapters.azure.openai_extractor import DOCUMENT_EXTRACTION_SCHEMA
from nodo_api.adapters.azure.openai_hierarchy import PARENT_SCHEMA
from tests.test_preflight import _strict_problems


@pytest.mark.parametrize("schema", [PARENT_SCHEMA, DOCUMENT_EXTRACTION_SCHEMA])
def test_parent_and_document_schemas_obey_the_strict_structured_output_rules(schema):
    assert _strict_problems(schema) == []
