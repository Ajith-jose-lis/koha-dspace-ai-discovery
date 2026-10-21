# Koha–DSpace Evidence-Grounded AI Discovery

An open-source research prototype combining Koha, DSpace, VuFind/Solr, Qdrant and FastAPI for evidence-grounded conversational library discovery.

## Article and Preprint

[**Evidence Grounded AI Discovery for Libraries: Design and Formative Validation of a Koha DSpace VuFind and Qdrant Prototype**](https://doi.org/10.5281/zenodo.23532168)

Ajith Jose. Version 1.1, 21 October 2026. Zenodo. **Preprint — not peer reviewed.**

The article documents the prototype architecture, implementation, preliminary mixed-source retrieval checks, limitations and a protocol for future GPT–Llama evaluation. It does not report a completed comparative model study.

**DOI:** [10.5281/zenodo.23532168](https://doi.org/10.5281/zenodo.23532168) · [Read or download the PDF on Zenodo](https://zenodo.org/records/23532168)

## Project Goal

The prototype investigates whether AI-generated library discovery answers can remain grounded in validated evidence from connected Koha and DSpace collections.

VuFind remains the mandatory public interface. Standard search continues to work when AI is OFF or unavailable. AI answers appear above, not instead of, normal search results.

## Prototype Scope

Current prototype:

- Koha test catalogue
- DSpace open-access PDFs
- VuFind unified discovery
- Solr standard search
- Qdrant semantic passage retrieval
- FastAPI middleware
- OpenAI and local Ollama/Llama providers

This is a research prototype, not a production deployment.

## Architecture

Patron -> VuFind -> Solr for standard search
Patron -> VuFind -> FastAPI -> Qdrant -> selected LLM provider for AI answers

Koha and DSpace supply the library and repository content used by the discovery layer.

## Evidence-Grounded Flow

1. The patron submits a question in VuFind.
2. Standard VuFind/Solr search remains available independently.
3. FastAPI retrieves candidate evidence from Qdrant.
4. Evidence is limited and validated before generation.
5. Only validated evidence is supplied to the selected model.
6. The model is instructed to answer only from that evidence.
7. Citation references are validated before the answer is returned.
8. If evidence is insufficient, the system returns:

> I could not find sufficient supporting evidence in the connected library collections.


## Current Models

- OpenAI: `gpt-6-luna`
- Ollama: `llama3.1:8b`

The models are compared as contrasting deployment approaches, not as capability-equivalent models.

## Research Logging

Research runs record:

- query
- provider
- model
- generated answer
- exact evidence snapshot
- retrieved sources
- cited source numbers
- token use
- refusal status
- latency

## Security Principles

- no API keys or passwords in GitHub
- no patron identity or borrowing history sent to external LLMs
- no direct database, shell or SQL access for LLMs
- Qdrant and FastAPI remain internally controlled
- rate limits and output limits are enforced
- citations are validated
- secrets, databases, backups and logs are excluded from Git

## Repository Structure

- `config/` — safe example configuration
- `docs/` — architecture, security and research documentation
- `middleware/` — FastAPI evidence-grounded AI service
- `research/` — controlled research runner
- `retrieval/` — Qdrant evidence indexing
- `scripts/data-preparation/` — test-data preparation utilities
- `tests/` — retrieval and citation tests
- `vufind/kdai-theme/` — custom VuFind theme

## Software Versions

Verified software versions used by the prototype are documented in `docs/software-versions.md`.

## Project Status

Current status: active research prototype.

Completed areas include Koha test data, DSpace open-access content, VuFind unified discovery, Qdrant passage retrieval, FastAPI middleware, OpenAI and Ollama provider selection, evidence validation, citation validation, research logging and private mobile HTTPS testing.

Remaining work includes final reproducibility documentation, research-question design, comparative evaluation, security testing, screenshots and demonstration material.

## Research Direction

Tentative paper title:

**Evidence-Grounded Conversational Discovery Across Koha and DSpace: Comparing a Proprietary Frontier LLM with a Locally Hosted Open-Source Model**

Research results will only be reported after human verification.

## AI Assistance

AI tools may assist with coding, debugging and documentation. All research claims and publication content remain subject to human verification and journal disclosure requirements.
