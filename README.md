# 9XAIPal (V1)

[![Video demo](https://img.youtube.com/vi/m-uIaNKOOrk/0.jpg)](https://youtu.be/m-uIaNKOOrk)

---

## What is it?

**9XAIPal** is a local-first reading companion for **research papers, technical books, and long-form PDFs**. Drop a file and it structurally extracts headings, math, tables, and figures.

A **paper** then reads as one continuous article. Highlight a passage, a figure, or an equation and ask about it — the answer arrives as a note in the margin, beside the thing it is about. A **book** keeps the chapter-by-chapter reader and its side chat.

---

## Why I built it

Reading dense material is cognitively expensive — whether it's a 12-page paper or a 600-page textbook. Most people skim and retain surprisingly little. Research in **cognitive load theory** and the **segmented learning** literature shows that breaking content into small, labeled units — and pairing it with **generative questioning** — significantly improves comprehension and retention over passive scrolling.

9XAIPal adapts to what you uploaded:
- **Research papers** — the whole paper as an article, with questions anchored where you asked them. Nothing is gated behind a keypress; the interruption is yours to choose.
- **Books** — read one *chapter* at a time. The system detects major chapter boundaries and lets you study an entire chapter as a single unit, so you stay in narrative flow while still avoiding wall-of-text overwhelm.

Either way you learn by asking, not just by highlighting — and for papers the question and its answer stay pinned to the paragraph that provoked them.

Everything runs locally by default. Your documents and conversations never leave your machine unless you explicitly ask a question that requires live web search.

---

## Tech stack & why

| Layer | Technology | Why it was chosen |
|-------|-----------|-------------------|
| **Frontend** | Vite + React 19 + Tailwind CSS + KaTeX | Fast dev/build cycle, precise math rendering, responsive dark/light mode |
| **API** | FastAPI + Pydantic v2 | Async Python backend, automatic validation, native OpenAPI docs |
| **Database** | PostgreSQL 16 + **pgvector** | ACID document storage; native vector similarity search so no extra vector DB is needed |
| **Embeddings** | Ollama (local) or OpenAI (cloud) | Local-first for privacy; cloud auto-fallback when the host is offline |
| **LLM** | Ollama (Gemma 4, etc.) or GPT-4o / Claude / Grok / DeepSeek | Same auto-fallback chain: local first, cloud only if needed — no config switching |
| **PDF extraction** | **MinerU** 3.x (with PyMuPDF fallback) | State-of-the-art structural extraction: OCR, table recognition, equation → LaTeX |
| **Background jobs** | Celery + Redis | Heavy extraction runs asynchronously so uploads never hang |
| **Web search** | SearXNG (self-hosted metasearch) | EXTERNAL answers without sending every query to a single commercial engine. **Being replaced** by Exa (semantic search) + Firecrawl (page → clean markdown) so the research agent reads sources instead of summarising search snippets — see [the migration plan](docs/plans/exa-firecrawl-research-stack.md) |
| **Vector index** | pgvector HNSW | Fast approximate nearest neighbors inside Postgres; no extra service to run |

---

## Features

- **Drag-and-drop PDF upload.** A paper is readable the moment MinerU and the chunker finish — no embedding pass, no summarization, nothing to wait for.
- **Article reading for papers** — the whole document at once, in serif prose, with KaTeX math, extracted figures, and real tables.
- **Margin notes** — highlight text, a figure, or an equation and ask. The answer streams into a card beside it, keeps the quote highlighted in the page, and offers jump chips back to the blocks it cites. Notes persist, thread with follow-ups, and can sit in either margin.
- **Your own notes and bookmarks** — write a note beside any passage, and mark as many places as you like. Bookmarks show as ribbons in the text and as ticks on the progress rail, so the paper carries a map of where you have been. One panel puts contents, bookmarks and every note behind a single search.
- **Decks** — drag one margin card onto another and they stack. The gutter's scarce resource is vertical space, and a deck spends one card's worth of it on several, so every card stays beside the passage that produced it. Turn on study mode and the deck hides each answer until you ask for it — the same stack becomes flashcards.
- **Everything follows you** — notes, bookmarks and decks live in Postgres, not the browser, so a paper opened from another device on the network carries all of it.
- **Answers without an index** — when a paper fits in the context window the model simply reads all of it. When it doesn't, an agent searches and reads across chunks until it has enough.
- **Pick the model per question** — any model Ollama can serve, local or cloud-hosted, chosen from the composer. Each answer is labelled, so you can ask two models the same thing and compare. Follow-ups stay on the model the note started with.
- **Book mode** — chapter-by-chapter study with the side chat, smart context routing (`LOCAL` / `GLOBAL` / `OVERVIEW` / `EXTERNAL`), pre-computed summaries, VLM figure descriptions, and conversation compaction.
- **Research agent** — iterative Observe → Reason → Act loop for deep external questions
- **Reading-order reconstruction** — fixes two-column and complex-layout papers via LLM
- **Glyph repair** — MinerU mangles inline math variables typeset with Unicode Mathematical Alphanumeric Symbols into `�`; the PDF is re-read to recover each one and re-emit it as LaTeX.

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

### 4. Or skip steps 2–3 and run the whole thing in Docker (recommended)
```bash
cd backend
docker compose up -d --build
```
Brings up Postgres, Redis, SearXNG, the Celery worker, the API, and a one-shot container that
builds the SPA — then serves **the UI and the API on a single port**: <http://localhost:8000>.
No Node or Python needed on the host. Ollama stays on your machine.

If another project already holds `8000`, `5432`, `6379` or `8080`, override the host side in
`backend/.env` — `API_PORT`, `POSTGRES_PORT`, `REDIS_PORT`, `SEARXNG_PORT`.

Just the infrastructure, if you want to run the app from source:
```bash
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

## Documentation

Full docs live in [`docs/`](docs/) — start at [`docs/README.md`](docs/README.md), which routes you
to the right document by task.

| Want to… | Go to |
| --- | --- |
| Get it running | [docs/01-orientation/setup.md](docs/01-orientation/setup.md) |
| Fix something broken | [docs/01-orientation/operations.md](docs/01-orientation/operations.md) |
| Understand the system | [docs/02-architecture/overview.md](docs/02-architecture/overview.md) |
| Look something up | [docs/03-reference/](docs/03-reference/) |
| See what's missing | [docs/roadmap.md](docs/roadmap.md) |

A sample paper for testing ingestion ships at
[`samples/attention-is-all-you-need.pdf`](samples/).

---

## Repository

[https://github.com/Khaled-Saleh-KL1/9XAIPal.git](https://github.com/Khaled-Saleh-KL1/9XAIPal.git)
