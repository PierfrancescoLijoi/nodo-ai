# ADR-008: Multilingual strategy for en, pl, it, da

Status: accepted

## Decision
Keep UI locale, spoken language and content language as separate fields. i18next with English as the key source and a CI parity check. Plural keys for every count. Legal text versioned per locale and human reviewed. Multilingual dense retrieval for cross-lingual questions, language-aware lexical analyzers per content language. Answers follow the UI locale with verbatim source quotes.

## Consequences
- Adding a fifth language is a resource file, an analyzer mapping and golden-set cases.
- Evaluation reports faithfulness per language.
