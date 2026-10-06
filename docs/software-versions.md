# Software Versions

Verified on 5 October 2026.

## Host Platform

| Component | Version |
|---|---:|
| Debian GNU/Linux | 12 (bookworm) |
| Apache HTTP Server | 2.4.68 |
| Docker | 29.8.1 |

## Library Systems

| Component | Version |
|---|---:|
| Koha | 26.05.03-1 |
| DSpace Backend | 10.1 |
| DSpace Angular | 10.1 |
| PostgreSQL | 15 |
| VuFind | 11.1.0 |

## Search and Vector Infrastructure

| Component | Version |
|---|---:|
| VuFind Solr | 9.8.1 |
| DSpace Solr | 9.8.1 |
| Qdrant Server | 1.19.1 |
| qdrant-client | 1.19.1 |

## AI / RAG Middleware

| Component | Version |
|---|---:|
| Python | 3.11.2 |
| FastAPI | 0.142.2 |
| Uvicorn | 0.54.0 |
| OpenAI Python SDK | 3.22.1 |
| FastEmbed | 0.8.1 |
| HTTPX | 0.28.1 |
| Ollama | 0.35.0 |

## Configured Models

| Provider | Model |
|---|---|
| OpenAI | gpt-6-luna |
| Ollama | llama3.1:8b |

The two models are used as contrasting deployment options rather than treated as capability-equivalent models.

## Retrieval Configuration

- Embedding model: `BAAI/bge-small-en-v1.5`
- Qdrant collection: `kdai_evidence_v1`
- Evidence passages supplied to answer generation: 4
- Maximum passages per document: 2
- Evidence text budget per passage: 500 characters
- Minimum evidence score: 0.58

## Runtime Architecture Notes

- VuFind uses its own Solr instance on localhost.
- DSpace runs with a separate Solr instance.
- Qdrant is used for semantic passage retrieval.
- FastAPI is bound to localhost and is accessed by VuFind through Apache.
- Ollama is bound locally for the Llama provider.
- API credentials and service secrets are stored outside the public repository.
