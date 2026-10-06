# Research Methodology

## Aim

To evaluate evidence-grounded conversational discovery across Koha and DSpace using the same retrieval evidence and citation rules with different LLM providers.

## Prototype Scope

- Koha test catalogue with approximately 100–200 records.
- DSpace test repository with 10–20 legally usable open-access PDFs.
- VuFind as the unified public search interface.
- Solr for standard search.
- Qdrant for semantic passage retrieval.
- FastAPI for evidence validation, provider selection, citation validation and research logging.

## Model Comparison

The current comparison uses:

- OpenAI `gpt-6-luna` as a hosted proprietary model.
- Ollama `llama3.1:8b` as a practical locally hosted open-source model.

The models are not treated as capability-equivalent. The comparison is intended to examine contrasting deployment approaches under the same evidence-grounded conditions.

## Controlled Conditions

For matched research runs, the intention is to keep the following constant where practical:

- user question
- retrieval pipeline
- retrieved evidence
- prompt instructions
- citation rules
- evidence limits
- answer-length constraints

Provider-specific token settings may differ where required by provider behavior and should be documented.

## Planned Question Set

Target: approximately 50 verified research questions.

Where practical, each question should be run three times per model.

Questions should include:

- answerable factual questions
- questions requiring multiple evidence passages
- citation-sensitive questions
- insufficient-evidence questions
- ambiguous or broad discovery questions
- Koha-focused catalogue questions
- DSpace-focused full-text questions

## Logged Metrics

The system records:

- correctness-related evidence context
- exact evidence snapshot supplied to the model
- generated answer
- citations used
- refusal behavior
- unsupported claims for later human assessment
- latency
- token usage
- provider and model

## Human Evaluation

The human researcher remains responsible for evaluating:

- correctness
- faithfulness to evidence
- citation accuracy
- page accuracy
- unsupported claims
- appropriate refusals
- answer usefulness

No comparative result should be reported until the underlying evidence and outputs have been manually verified.

## Research Logging

Research runs are stored locally in the prototype research database.

The exact evidence snapshot used for each run is retained so later evaluation can compare the answer directly with the evidence supplied to the model.

Research databases and logs are not included in the public GitHub repository.

## Reproducibility

The public repository should document:

- software versions
- configuration defaults
- retrieval parameters
- model identifiers
- prompt and citation rules
- test question templates
- evaluation criteria

## Publication Ethics

AI assistance in coding, debugging, documentation or drafting must be disclosed according to the policy of the selected journal or publication venue.

The human researcher remains the author and is responsible for verifying all research claims and results.
