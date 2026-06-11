# 9XAIPal (V1)

> 🎬 **Video demo** — [Watch on YouTube](https://youtu.be/m-uIaNKOOrk)

---

## What is it?

**9XAIPal** is a local-first reading companion for **research papers, technical books, and long-form PDFs**. Drop a file and it structurally extracts headings, math, tables, and figures — then serves them back one piece at a time while you ask grounded, citation-backed questions in the side chat.

---

## Why I built it

Reading dense material is cognitively expensive — whether it's a 12-page paper or a 600-page textbook. Most people skim and retain surprisingly little. Research in **cognitive load theory** and the **segmented learning** literature shows that breaking content into small, labeled units — and pairing it with **generative questioning** — significantly improves comprehension and retention over passive scrolling.

9XAIPal adapts the granularity to what you uploaded:
- **Research papers** — read one *structural* chunk at a time: headings, equations, figures, tables.
- **Books** — read one *chapter* at a time. The system detects major chapter boundaries and lets you study an entire chapter as a single unit, so you stay in narrative flow while still avoiding wall-of-text overwhelm.

In both cases, the side chat answers questions using only the context you've actually seen (or the full document for broad questions), so you learn by explaining, not just by highlighting.

Everything runs locally by default. Your documents and conversations never leave your machine unless you explicitly ask a question that requires live web search.

---

## Tech stack & why

| Layer | Technology | Why it was chosen |
|-------|-----------|-------------------|
| **Frontend** | Vite + React 19 + Tailwind CSS + KaTeX | Fast dev/build cycle, precise math rendering, responsive dark/light mode |
| **API** | FastAPI + Pydantic v2 | Async Python backend, automatic validation, native OpenAPI docs |
| **Database** | PostgreSQL 16 + **pgvector** | ACID document storage; native vector similarity search so no extra vector DB is needed |
| **Embeddings** | Ollama (local) or OpenAI / Gemini (cloud) | Local-first for privacy; cloud auto-fallback when the host is offline |
| **LLM** | Ollama (Gemma 4, etc.) or GPT-4o / Claude / Gemini / Grok / DeepSeek | Same auto-fallback chain: local first, cloud only if needed — no config switching |
| **PDF extraction** | **MinerU** 3.x (with PyMuPDF fallback) | State-of-the-art structural extraction: OCR, table recognition, equation → LaTeX |
| **Background jobs** | Celery + Redis | Heavy extraction and embedding runs asynchronously so uploads never hang |
| **Web search** | SearXNG (self-hosted metasearch) | EXTERNAL answers without sending every query to a single commercial engine |
| **Vector index** | pgvector HNSW | Fast approximate nearest neighbors inside Postgres; no extra service to run |

---

## Features

- **Drag-and-drop PDF upload** with live progress overlay (`extracting → chunking → embedding → summarizing`)
- **Dual reading modes** — the reader adapts to the document type:
  - **Paper mode** — granular chunk-by-chunk: headings, paragraphs, math blocks, tables, figures. Perfect for research papers with dense, mixed layouts.
  - **Book mode** — chapter-by-chapter study. The system detects major chapter boundaries and lets you read and discuss one chapter at a time, preserving narrative flow across longer texts.
- **Smart context routing** — the chat automatically picks the best source for each question:
  - `LOCAL` — current chunk / chapter + neighbors + inline images (multimodal)
  - `GLOBAL` — semantic vector search across the entire document
  - `OVERVIEW` — pre-computed hierarchical summaries (executive + H1 + H2)
  - `EXTERNAL` — live web search via SearXNG (only when the question demands it)
- **Research agent** — iterative Observe → Reason → Act loop for deep external questions
- **VLM figure descriptions** — AI-generated technical descriptions of diagrams and architectures
- **Reading-order reconstruction** — fixes two-column and complex-layout papers via LLM
- **Conversation memory with compaction** — long chats stay coherent without blowing the context window
- **Domain guardrail** — strictly CS / ML / AI / engineering by default; bridges to other fields only when you explicitly ask

---

## Quick start

### Prerequisites
- Python 3.11+
- Node.js 18+
- PostgreSQL 15+ *(or use the bundled Docker compose service)*
- Redis *(or use the bundled Docker compose service)*
- Ollama *(optional — a cloud API key works instead)*

### 1. Clone the repo
```bash
git clone https://github.com/Khaled-Saleh-KL1/9XAIPal.git
cd 9XAIPal
```

### 2. Start the backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Copy and edit environment variables
cp .env.example .env

# Start the API
uvicorn app.main:app --reload --port 8000

# Start the Celery worker in a separate terminal (same venv)
celery -A app.core.celery_app worker --loglevel=info
```

### 3. Start the frontend
```bash
cd frontend
npm install
npm run dev      # opens at http://localhost:5173
```

### 4. Run services via Docker (recommended)
```bash
cd backend
docker compose up -d postgres redis searxng
```

### 🌐 LAN server mode
To let any device on the **same Wi-Fi** use the app, run the bundled script:
```bash
cd backend
./start-lan-server.sh
```
It builds the full stack, removes upload limits, prints the exact LAN URL, and tears everything down cleanly on `Ctrl+C`.

---

## Repository

[https://github.com/Khaled-Saleh-KL1/9XAIPal.git](https://github.com/Khaled-Saleh-KL1/9XAIPal.git)
