# Architecture

## Overview

The prototype separates standard library discovery from AI-assisted answer generation.

VuFind remains the public discovery interface and continues to provide normal Solr search independently of the AI service.

## Standard Search Path

Patron -> VuFind -> Solr -> Koha and DSpace metadata

Standard search remains functional when AI is OFF, unavailable or rate-limited.

## AI Answer Path

Patron -> VuFind -> FastAPI -> Qdrant -> validated evidence -> selected LLM provider -> citation validation -> VuFind

## Component Responsibilities

- Koha: catalogue records, bibliographic metadata, items, call numbers and availability.
- DSpace: repository metadata and open-access PDF content.
- VuFind: mandatory unified public search and presentation layer.
- Solr: standard keyword/faceted discovery.
- Qdrant: semantic passage retrieval for evidence-grounded answering.
- FastAPI: controlled retrieval, evidence validation, provider selection, citation validation, rate limiting and research logging.
- OpenAI adapter: hosted proprietary model provider.
- Ollama adapter: locally hosted Llama provider.

## Evidence Boundary

The LLM is not treated as a source of library evidence.

Only evidence retrieved from the connected collections is supplied for grounded answer generation.

The exact evidence snapshot used for each research run is recorded for later human verification.

## Failure Behaviour

If evidence is insufficient, the system returns:

> I could not find sufficient supporting evidence in the connected library collections.

Normal VuFind search results remain available below the AI answer area.

## Provider Comparison

The provider layer is replaceable. The current prototype compares OpenAI `gpt-6-luna` with locally hosted `llama3.1:8b` under the same retrieval and citation conditions.

The models are not assumed to be capability-equivalent.

## Current Deployment Boundaries

- FastAPI is bound to localhost.
- Qdrant is not exposed publicly.
- Ollama is bound locally.
- VuFind is accessed through Apache.
- mobile testing is currently performed through private HTTPS access.
- no LLM has direct SQL, shell or unrestricted database access.

## AI Timeout Coordination

The AI request path uses coordinated timeouts so slower local models can complete without exposing internal services directly.

- Ollama/FastAPI generation timeout: 300 seconds.
- Apache `/kdai-ai/` reverse-proxy timeout: 320 seconds.
- VuFind browser timeout for the local Llama provider: 330 seconds.

The proxy timeout must be longer than the backend generation timeout, and the browser timeout must be longer than the proxy timeout.

This prevents a successful long-running Llama response from being abandoned by Apache or the browser before completion.
