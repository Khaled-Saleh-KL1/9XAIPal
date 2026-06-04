# 9XAIPal

<img src="https://img.shields.io/badge/python-3.11+-blue?logo=python" alt="Python 3.11+"/> <img src="https://img.shields.io/badge/react-19-61DAFB?logo=react" alt="React 19"/> <img src="https://img.shields.io/badge/fastapi-0.115+-00A86B?logo=fastapi" alt="FastAPI"/> <img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT"/>

9XAIPal is a **reading companion for research papers**. Upload a PDF and ask questions about it — the app understands the paper's content and answers you in plain language, pointing you to the relevant sections and figures. It keeps conversations organized with threaded replies, automatically compresses long chats so it never loses track of context, and runs entirely on your own machine.

---

## Features

- **PDF Ingestion** — Upload any research paper (PDF). MinerU extracts text, LaTeX math, tables, and figures with high fidelity.
- **Structural Reading** — Papers are revealed chunk-by-chunk (headings, paragraphs, math blocks, tables, figures) for focused deep reading.
- **Smart Question Answering** — Ask questions about what's on screen (LOCAL), the full paper (GLOBAL), or the wider web (EXTERNAL). The system automatically picks the right context.
- **Inline Paper Figures** — When you ask about figures or diagrams, the model sees them and embeds them directly in its response.
- **Threaded Conversations** — Branch off into sub-threads for tangents without polluting the main discussion. Compaction keeps long chats from overflowing context.
- **Citation Chips** — Every answer cites its sources. Click a citation to jump to the relevant chunk.
- **Research Agent** — For complex queries, an iterative research loop searches the web and reads paper sections to synthesize thorough answers.
- **Local-First** — Everything runs on your machine: the LLM, embeddings, database, and search proxy. No data ever leaves your computer.

---

## Architecture Overview

```
Browser ──► FastAPI ──► PostgreSQL + pgvector
                │              │
                ├── Ollama (LLM + VLM + Embeddings)
                ├── SearXNG (web search proxy)
                └── Celery + MinerU (PDF extraction)
```

The frontend is a Vite + React 19 SPA. The backend is a FastAPI async server with Celery workers for heavy lifting (PDF extraction, embedding generation, summarization). PostgreSQL with pgvector handles both relational data and vector similarity search. Ollama provides the chat, vision-language, and embedding models. SearXNG is a local metasearch engine used only when the question requires external information.

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- Docker and Docker Compose (for Postgres, Redis, SearXNG)
- [Ollama](https://ollama.com) (for LLM + embedding models)
- [MinerU](https://github.com/opendatalab/MinerU) 3.2+ (`pip install mineru`)

### 1. Clone the Repository

```bash
git clone https://github.com/Khaled-Saleh-KL1/9XAIPal.git
cd 9XAIPal
```

### 2. Configure Environment

```bash
cp backend/.env.example backend/.env
# Edit backend/.env with your settings (secrets, model names, etc.)
```

### 3. Start Ancillary Services (Postgres, Redis, SearXNG)

```bash
cd backend
docker compose up -d postgres redis searxng
```

### 4. Pull LLM Models (One-Time)

```bash
ollama pull qwen3.5:cloud    # Or your preferred chat/VLM model
ollama pull nomic-embed-text # Embedding model (768-dim)
```

### 5. Start the Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload --port 8000

# In a separate terminal, start the Celery worker:
cd backend
source .venv/bin/activate
celery -A app.core.celery_app worker --loglevel=info
```

### 6. Start the Frontend

```bash
cd frontend
npm install
npm run dev  # Opens at http://localhost:5173
```

### 7. Verify It Works

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok","database":"ok","ollama":"ok","searxng":"ok"}
```

Open `http://localhost:5173` in your browser, drag a PDF onto the library, wait for ingestion to complete, then click into the paper and start asking questions.

### Docker-Only Setup

For a fully containerized experience, see `backend/docker-compose.yml`. The celery worker has its own Dockerfile (`Dockerfile.mineru`) with MinerU and PyTorch pre-installed.

```bash
cd backend
docker compose up -d --build
```

### LAN Server (Share Across Your Network)

Run `backend/start-lan-server.sh` to turn your machine into a temporary server that any device on the **same local network** (same Wi-Fi or Ethernet) can reach. The script:

- Brings up the full Docker stack (API, UI, Postgres, Redis, SearXNG, Celery worker)
- Builds the React frontend inside a container (no Node.js needed on the host)
- Removes the upload size limit for large PDFs
- Prints the LAN URL (e.g. `http://192.168.1.42:8000`) to open from other devices
- On Ctrl+C, shuts everything down while preserving your data

```bash
cd backend
./start-lan-server.sh
```

> **Note:** This only works on the **same local network** — the script detects your private LAN IP (e.g. `192.168.x.x` or `10.x.x.x`), which is not reachable from the internet. To expose the app over the internet you would need port forwarding on your router, a public IP, or a tunnel service like ngrok.

---

## Documentation

Detailed documentation for every component lives in the `docs/` directory:

| Document | Description |
|----------|-------------|
| [docs/setup.md](docs/setup.md) | Quick-start setup guide |
| [docs/architecture.md](docs/architecture.md) | System architecture overview |
| [docs/api-reference.md](docs/api-reference.md) | Full HTTP API reference |
| [docs/database-schema.md](docs/database-schema.md) | Every table, column, and index |
| [docs/ingestion-pipeline.md](docs/ingestion-pipeline.md) | PDF upload through embedding |
| [docs/chat-and-ask.md](docs/chat-and-ask.md) | Chat orchestration and context routing |
| [docs/frontend.md](docs/frontend.md) | Frontend component architecture |
| [docs/storage-and-static-files.md](docs/storage-and-static-files.md) | Disk layout and static serving |
| [docs/Architecture_Technical.md](docs/Architecture_Technical.md) | Deep technical dive |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | Vite 6, React 19, TypeScript, Tailwind CSS, KaTeX |
| **Backend** | FastAPI (Python 3.11+), Pydantic v2, SQLAlchemy |
| **Database** | PostgreSQL 16 + pgvector (768-dim vectors) |
| **Task Queue** | Celery 5+ with Redis broker |
| **LLM** | Ollama (Qwen 3.5 / Gemma 4 / any Ollama-hosted model) |
| **Embedding** | Ollama (nomic-embed-text, 768-dim) |
| **PDF Extraction** | MinerU 3.2+ |
| **Web Search** | SearXNG (local metasearch proxy) |
| **Containerization** | Docker Compose (Postgres, Redis, SearXNG, Celery worker) |

---

## Project Structure

```
9XAIPal/
├── backend/             # FastAPI app + Celery workers
│   ├── app/
│   │   ├── api/         # HTTP endpoints (health, papers, chunks, ask, search)
│   │   ├── chat/        # Chat orchestration, routing, prompts
│   │   ├── core/        # Config, lifecycle, paths, Celery wiring
│   │   ├── database/    # Schema, migrations, repositories
│   │   ├── embeddings/  # Embedding model wrapper
│   │   ├── extraction/  # PDF ingestion pipeline (MinerU + chunker)
│   │   ├── llm/         # Ollama client, VLM client, multimodal
│   │   ├── schemas/     # Pydantic request/response models
│   │   ├── search/      # SearXNG client + result ranking
│   │   ├── services/    # Use-case layer
│   │   ├── summarization/ # Section + figure summarization
│   │   └── workers/     # Celery task definitions
│   ├── docker-compose.yml
│   ├── Dockerfile
│   ├── Dockerfile.mineru
│   └── tests/
├── frontend/            # Vite + React 19 SPA
│   └── src/
│       ├── views/       # LibraryView, ReadingView, ChatPane, etc.
│       ├── components/  # Shared UI components
│       └── api.ts       # Typed HTTP client
└── docs/                # Comprehensive documentation
```

---

## License

MIT