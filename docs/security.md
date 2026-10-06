# Security

## Scope

This document describes security controls for the research prototype. It is not a production security certification.

## Secrets Management

- API keys, passwords and service credentials are stored outside the public repository.
- `.env` files, databases, backups, logs and private keys are excluded by `.gitignore`.
- the public repository contains only placeholder values in `config/example.env`.
- secret directories and sensitive runtime files use restricted filesystem permissions.

## Network Boundaries

- FastAPI is bound to localhost.
- Qdrant is not exposed publicly.
- Ollama is bound locally.
- Koha staff access is not exposed through the public discovery path.
- VuFind is the intended public-facing discovery interface.
- current mobile testing uses private HTTPS access rather than direct exposure of internal service ports.

## LLM Boundaries

- LLMs do not receive direct SQL access.
- LLMs do not receive shell access.
- LLMs do not receive unrestricted API access.
- only validated evidence is supplied for grounded answer generation.
- patron identity and borrowing history must not be sent to external LLM providers.

## Retrieval and Output Controls

- evidence retrieval is limited before generation.
- maximum passages per document are constrained.
- evidence text length is constrained.
- a minimum evidence score is applied.
- model output length is constrained.
- citation numbers are validated before returning an answer.
- insufficient evidence produces a controlled refusal.

## Rate Limiting

- patron requests are limited per IP per hour.
- a global daily request limit is enforced.
- the local research runner bypasses patron rate limits only for controlled research execution.

## Research Logging

Research logging records the exact evidence snapshot used for each run together with provider, model, answer, citations, token use, refusal status and latency.

Research logs must not contain API keys, passwords or patron-identifying borrowing data.

## GitHub Release Controls

Before each public release:

1. run a secret scan on the release tree.
2. verify no `.env`, database, backup, log or credential files are staged.
3. inspect `git status` and the staged diff.
4. confirm example configuration contains placeholders only.
5. verify dataset and PDF redistribution rights before adding data files.

## Future Agentic Features

Any future hold, renewal, interlibrary-loan or other data-changing action must require authentication, authorization checks, explicit user confirmation and audit logging.

Read-only agentic tools should use validated inputs, timeouts, rate limits and maximum step counts.
