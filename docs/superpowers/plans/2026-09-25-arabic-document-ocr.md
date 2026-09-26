# Arabic Document OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate, sandbox-tested Arabic document ingestion route that
keeps English MinerU/VLM behavior unchanged, extracts printed and mixed Arabic
with Gemini 3.7 Flash, continues validated pages with Gemma 4 after Gemini
exhaustion, and clearly blocks handwritten OCR until Gemini Pro billing is
enabled.

**Architecture:** A local Arabic router classifies every PDF before extraction.
English stays on the existing extractor dispatcher; printed or Arabic/English
mixed documents enter a new page-addressed OCR orchestrator; handwritten and
uncertain decisions stop without an OCR provider call. The Arabic orchestrator
normalizes Gemini and Gemma output into the existing `content_list.json`
contract, records per-page provenance, repairs only Gemma pages, and rejoins the
existing chunk/persistence path.

**Tech Stack:** Python 3.11, FastAPI, Celery, SQLAlchemy/PostgreSQL, PyMuPDF,
`google-genai`, Ollama HTTP API, pytest, React 19, TypeScript, Vitest, React
Testing Library, Docker Compose.

**Spec:**
[`docs/superpowers/specs/2026-09-25-arabic-document-ocr-design.md`](../specs/2026-09-25-arabic-document-ocr-design.md)

## Global Constraints

- Treat every shell code block as starting from the feature worktree root;
  return there before running the next checklist step.
- Implement in an isolated worktree and feature branch; do not develop on
  `main`.
- Do not change the output or routing behavior of existing English MinerU,
  English VLM, book, article, or web-search pipelines.
- `ARABIC_OCR_ENABLED=false` and `ARABIC_HANDWRITTEN_OCR_ENABLED=false` by
  default.
- Printed OCR model: `gemini-3.7-flash` with `thinking_level="low"`, the
  lowest supported level. The current Gemini API does not offer a true
  thinking-off setting for 3.7 Flash, so thought tokens may still be billed;
  this is the closest supported configuration to the requested no-thinking
  mode while keeping the selected model.
- Handwritten model: `gemini-3.1-pro-preview`, configured but never called while
  the handwritten feature flag is false.
- Local router model: `qwen3-vl:4b-instruct`; Gemma fallback:
  `gemma4:31b-cloud`.
- English-only documents are LTR. Any document with verified substantive
  Arabic body text, including Arabic/English mixed text, takes the Arabic route
  and uses an RTL base direction.
- Never splice providers inside a page. Preserve every independently validated
  Gemini page and begin Gemma at the first uncommitted page.
- Repair only Gemma-origin pages. Never normalize, spell-correct, or otherwise
  rewrite Gemini-origin OCR.
- Never commit, log, return, or persist API keys. Put live keys only in ignored
  `backend/.env` during sandbox execution.
- Do not call Gemini Pro during this implementation or its live tests.
- Backend tests must use a database whose name contains `test`; the suite
  truncates document data.

## Review Focus

1. **Printed Arabic that resembles handwriting:** it must auto-route as printed
   or abstain, never auto-block as handwritten; Task 3 adds this regression.
2. **A Gemini response truncated after complete page markers:** validated pages
   must survive and Gemma must start at the next page; Task 7 pins this.
3. **Mixed Arabic/English with mostly English characters:** verified Arabic
   body text must still select the Gemini/RTL route; Tasks 3 and 10 pin this.
4. **A one-page handwritten scan:** full-page and region votes must agree before
   the unavailable state is shown; Task 3 pins this.
5. **Secrets in failures/logs:** key indices may be logged, key material may
   not; Task 4 checks captured logs and Task 13 scans the final diff.

---

## File Structure

### New backend modules

- `backend/app/extraction/arabic_types.py` — shared enums, dataclasses, typed
  errors, usage, and page provenance.
- `backend/app/extraction/arabic_classifier.py` — all-page text/visual language
  and writing-style classification through local Ollama only.
- `backend/app/extraction/gemini_ocr_client.py` — Gemini SDK request construction,
  key cascade, bounded retries, usage capture, and failure categories.
- `backend/app/extraction/arabic_adapter.py` — page-marker validation and
  Markdown-to-`content_list.json` conversion.
- `backend/app/extraction/arabic_fallback.py` — Gemma 4 printed OCR calls through
  the Ollama key cascade.
- `backend/app/extraction/arabic_repair.py` — conservative, provenance-gated
  Gemma cleanup.
- `backend/app/extraction/arabic_ocr.py` — rendering, batching, Gemini-to-Gemma
  continuation, atomic artifacts, and progress.
- `backend/eval/arabic_documents/{run_eval.py,scoring.py,README.md}` — local
  classification/OCR evaluation harness and operator instructions.
- `backend/eval/__init__.py`, `backend/eval/arabic_documents/__init__.py` —
  make the evaluation harness executable with `python -m`.

### Existing backend files changed

- `backend/pyproject.toml`, `backend/uv.lock` — add and lock `google-genai`.
- `backend/app/core/config.py`, `backend/.env.example`,
  `backend/docker-compose.yml`, `backend/docker-compose.prod.yml` — Arabic-only
  settings and container forwarding; no credentials.
- `backend/app/database/schema.sql`, `backend/app/database/migrations.py` —
  nullable classification, direction, provenance summary, and ingestion error
  code.
- `backend/app/schemas/documents.py`,
  `backend/app/database/repositories/documents.py` — API metadata and latest-job
  error fields.
- `backend/app/extraction/jobs.py`, `backend/app/services/ingestion.py`,
  `backend/app/extraction/pipeline_sync.py` — typed failure plumbing, manual
  requeue, Arabic routing, and the unchanged English branch.
- `backend/app/api/v1/endpoints/documents.py` — progress metadata and writing
  style confirmation endpoint.
- `backend/app/api/v1/endpoints/chunks.py` — direction/classification metadata
  on the whole-document reader response.

### Existing frontend files changed

- `frontend/src/api.ts` — Arabic metadata, error/action fields, confirmation
  client, and full-document direction.
- `frontend/src/App.tsx`, `frontend/src/views/ProcessingOverlay.tsx`,
  `frontend/src/views/LibraryView.tsx` — persistent unsupported/uncertain states
  and confirmation controls.
- `frontend/src/views/ArticleReader.tsx`,
  `frontend/src/views/ArticleBlock.tsx`, `frontend/src/index.css` — block-level
  RTL/LTR behavior without changing application chrome.
- `frontend/package.json`, `frontend/package-lock.json`,
  `frontend/vite.config.ts` — Vitest/Testing Library test support.

### Tests added

- `backend/tests/test_arabic_config.py`
- `backend/tests/test_arabic_persistence.py`
- `backend/tests/test_arabic_classifier.py`
- `backend/tests/test_gemini_ocr_client.py`
- `backend/tests/test_arabic_adapter.py`
- `backend/tests/test_arabic_repair.py`
- `backend/tests/test_arabic_ocr_fallback.py`
- `backend/tests/test_arabic_pipeline_routing.py`
- `backend/tests/test_arabic_confirmation_api.py`
- `backend/tests/test_arabic_document_api.py`
- `backend/tests/test_arabic_scoring.py`
- `frontend/src/lib/documentDirection.ts`
- `frontend/src/lib/documentDirection.test.ts`
- `frontend/src/views/ProcessingOverlay.test.tsx`
- `frontend/src/test/setup.ts`

---

### Task 1: Isolate the branch and add configuration foundations

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Modify: `backend/docker-compose.yml`
- Modify: `backend/docker-compose.prod.yml`
- Test: `backend/tests/test_arabic_config.py`

**Interfaces:**
- Consumes: existing `Settings._split_keys(raw: str) -> list[str]`.
- Produces: `settings.gemini_api_keys: list[str]` and all `arabic_*` settings
  used by Tasks 3–9.

- [x] **Step 1: Create the isolated execution worktree**

```bash
git status --short
git worktree add ../9XAIPal_VPS-arabic-ocr -b feat/arabic-document-ocr
cd ../9XAIPal_VPS-arabic-ocr
```

Expected: the new worktree starts at the approved plan commit. Do not copy the
untracked root `image.png` or `pyproject.toml` into it.

- [x] **Step 2: Run the unchanged baseline**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_ingestion_pipeline.py tests/test_vlm_extractor.py tests/test_multi_key_rotation.py -q
cd ../frontend
npm run build
```

Expected: all selected backend tests pass and the frontend production build
finishes successfully. If not, stop and report the pre-existing failure.

- [x] **Step 3: Write failing settings tests**

```python
# backend/tests/test_arabic_config.py
from app.core.config import Settings


def test_gemini_keys_are_trimmed_and_empty_values_are_dropped():
    cfg = Settings(gemini_api_keys_raw=" k1, ,k2,\nk3 ")
    assert cfg.gemini_api_keys == ["k1", "k2", "k3"]


def test_arabic_features_are_safe_by_default():
    cfg = Settings()
    assert cfg.arabic_ocr_enabled is False
    assert cfg.arabic_handwritten_ocr_enabled is False
    assert cfg.arabic_gemini_printed_model == "gemini-3.7-flash"
    assert cfg.arabic_gemini_printed_thinking_level == "low"
    assert cfg.arabic_gemini_handwritten_model == "gemini-3.1-pro-preview"
```

- [x] **Step 4: Run the settings tests and verify failure**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_config.py -q
```

Expected: FAIL because the Arabic settings do not exist.

- [x] **Step 5: Add the exact settings and dependency**

```python
# backend/app/core/config.py — add beside extractor settings
arabic_ocr_enabled: bool = False
arabic_handwritten_ocr_enabled: bool = False
arabic_router_model: str = "qwen3-vl:4b-instruct"
arabic_router_base_url: str = "http://localhost:11434"
gemini_api_keys_raw: str = ""
arabic_gemini_printed_model: str = "gemini-3.7-flash"
arabic_gemini_printed_thinking_level: Literal["low"] = "low"
arabic_gemini_handwritten_model: str = "gemini-3.1-pro-preview"
arabic_gemma_fallback_model: str = "gemma4:31b-cloud"
arabic_ocr_dpi: int = 200
arabic_gemini_media_resolution: Literal["HIGH"] = "HIGH"
arabic_ocr_single_request_max_pages: int = 4
arabic_ocr_batch_pages: int = 4
arabic_ocr_max_output_tokens: int = 32768
arabic_min_body_char_count: int = 20
arabic_classifier_confidence_min: float = 0.80
arabic_handwritten_confidence_min: float = 0.97
arabic_classifier_batch_pages: int = 8

@property
def gemini_api_keys(self) -> list[str]:
    return self._split_keys(self.gemini_api_keys_raw)
```

Add `"google-genai"` to `backend/pyproject.toml`. Add the same variable names
to `backend/.env.example`, and forward them to both `api` and `celery_worker`
in both Compose files. Set the worker's default
`ARABIC_ROUTER_BASE_URL=http://host.docker.internal:11434`. Never add key
values to a tracked file.

- [x] **Step 6: Lock dependencies and pass the settings tests**

```bash
cd backend
uv lock
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_config.py -q
```

Expected: PASS.

- [x] **Step 7: Commit the foundation**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/core/config.py \
  backend/.env.example backend/docker-compose.yml backend/docker-compose.prod.yml \
  backend/tests/test_arabic_config.py
git commit -m "feat: add Arabic OCR configuration"
```

---

### Task 2: Persist classification, provenance, and typed failures

**Files:**
- Modify: `backend/app/database/schema.sql:25-120`
- Modify: `backend/app/database/schema.sql:388-410`
- Modify: `backend/app/database/migrations.py:80-180`
- Modify: `backend/app/schemas/documents.py:10-70`
- Modify: `backend/app/database/repositories/documents.py:90-130`
- Modify: `backend/app/extraction/jobs.py:6-28`
- Modify: `backend/app/services/ingestion.py:45-100`
- Modify: `backend/app/extraction/pipeline_sync.py:99-120`
- Test: `backend/tests/test_arabic_persistence.py`

**Interfaces:**
- Consumes: current `documents`, `ingestion_jobs`, and failed-to-queued job
  transition.
- Produces: nullable document metadata, `ingestion_jobs.error_code`,
  `requeue_failed_job(session, job_id) -> dict`, and status writers that set or
  clear both error fields.

- [x] **Step 1: Write failing migration and job-state tests**

```python
# backend/tests/test_arabic_persistence.py
import pytest
from sqlalchemy import text
from app.services.ingestion import requeue_failed_job


def test_arabic_columns_exist(db_session_sync):
    cols = db_session_sync.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name IN ('documents', 'ingestion_jobs')
    """)).scalars().all()
    for name in (
        "detected_language", "detected_writing_style", "text_direction",
        "classifier_model", "classification_confidence",
        "classification_source", "ocr_provider_summary", "error_code",
    ):
        assert name in cols


@pytest.mark.asyncio
async def test_requeue_clears_failure_fields(db_session, failed_job_id):
    row = await requeue_failed_job(db_session, failed_job_id)
    assert row["status"] == "queued"
    persisted = (await db_session.execute(text(
        "SELECT error_code, error_message, completed_at FROM ingestion_jobs WHERE id=:id"
    ), {"id": failed_job_id})).mappings().one()
    assert dict(persisted) == {
        "error_code": None, "error_message": None, "completed_at": None
    }
```

- [x] **Step 2: Run and verify failure**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_persistence.py -q
```

Expected: FAIL on missing columns and function.

- [x] **Step 3: Add idempotent schema and migration columns**

```sql
-- documents
detected_language TEXT,
detected_writing_style TEXT,
text_direction TEXT,
classifier_model TEXT,
classification_confidence REAL,
classification_source TEXT,
ocr_provider_summary JSONB,

-- ingestion_jobs
error_code TEXT,
```

Mirror each column with `ADD COLUMN IF NOT EXISTS` in `critical_alters`. Extend
`DocumentResponse` with matching optional fields plus
`job_error_code`/`job_error_message`. Extend the list query's lateral job
selection to expose those aliases.

- [x] **Step 4: Make job failures typed and reusable**

```python
# backend/app/services/ingestion.py
async def requeue_failed_job(session: AsyncSession, job_id: UUID) -> dict:
    await _reserve_queue_capacity(session)
    result = await session.execute(text("""
        UPDATE ingestion_jobs
        SET status='queued', error_code=NULL, error_message=NULL,
            started_at=NULL, completed_at=NULL, progress_fraction=NULL,
            created_at=NOW()
        WHERE id=:id AND status='failed'
        RETURNING id, document_id, status, created_at
    """), {"id": job_id})
    row = result.mappings().first()
    if not row:
        raise ValueError("only a failed ingestion job can be requeued")
    return dict(row)
```

Add `error_code: Optional[str] = None` to async and sync status writers. When
entering an active state, explicitly clear both error fields. When failing,
persist both. Keep all current callers source-compatible through defaults.

- [x] **Step 5: Pass focused and existing ingestion tests**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_persistence.py \
  tests/test_ingestion_pipeline.py tests/test_capacity.py -q
```

Expected: PASS.

- [x] **Step 6: Commit persistence plumbing**

```bash
git add backend/app/database backend/app/schemas/documents.py \
  backend/app/extraction/jobs.py backend/app/services/ingestion.py \
  backend/app/extraction/pipeline_sync.py backend/tests/test_arabic_persistence.py
git commit -m "feat: persist Arabic routing metadata"
```

---

### Task 3: Build the local classifier with an abstention state

**Files:**
- Create: `backend/app/extraction/arabic_types.py`
- Create: `backend/app/extraction/arabic_classifier.py`
- Test: `backend/tests/test_arabic_classifier.py`

**Interfaces:**
- Consumes: PyMuPDF and the Task 1 router settings.
- Produces:
  `classify_document(pdf_path: Path, vision_call: VisionCall | None = None) -> ClassificationDecision`.

- [x] **Step 1: Define the domain contract in failing tests**

```python
# backend/tests/test_arabic_classifier.py
from app.extraction.arabic_types import DocumentRoute
from app.extraction.arabic_classifier import classify_document


def test_mixed_body_text_uses_arabic_printed_route(mixed_text_pdf, fake_printed_vision):
    d = classify_document(mixed_text_pdf, vision_call=fake_printed_vision)
    assert d.route is DocumentRoute.ARABIC_PRINTED
    assert d.language == "mixed"
    assert d.text_direction == "rtl"


def test_handwriting_like_font_never_auto_blocks_when_votes_conflict(
    decorative_printed_pdf, conflicting_vision
):
    d = classify_document(decorative_printed_pdf, vision_call=conflicting_vision)
    assert d.route is DocumentRoute.ARABIC_STYLE_UNCERTAIN


def test_one_page_handwriting_requires_full_page_and_region_consensus(
    one_page_scan, split_vote_vision
):
    d = classify_document(one_page_scan, vision_call=split_vote_vision)
    assert d.route is DocumentRoute.ARABIC_STYLE_UNCERTAIN
```

Also add fixtures for English-only, scanned printed Arabic, confident
handwriting, isolated handwritten signatures on printed forms, and no-readable-
text images.

- [x] **Step 2: Run and verify import failure**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_classifier.py -q
```

Expected: FAIL because the classifier modules do not exist.

- [x] **Step 3: Add exact enums and result types**

```python
# backend/app/extraction/arabic_types.py
from dataclasses import dataclass, field
from enum import Enum

class DocumentRoute(str, Enum):
    ENGLISH = "english"
    ARABIC_PRINTED = "arabic_printed"
    ARABIC_HANDWRITTEN = "arabic_handwritten"
    ARABIC_STYLE_UNCERTAIN = "arabic_style_uncertain"

@dataclass(frozen=True)
class PageStyleVote:
    page_idx: int
    language: str
    writing_style: str
    confidence: float
    evidence: str = ""

@dataclass(frozen=True)
class ClassificationDecision:
    route: DocumentRoute
    language: str
    writing_style: str
    text_direction: str
    confidence: float
    classifier_model: str
    votes: tuple[PageStyleVote, ...] = field(default_factory=tuple)
```

- [x] **Step 4: Implement all-page local screening and conservative aggregation**

```python
# backend/app/extraction/arabic_classifier.py
def classify_document(pdf_path: Path, vision_call: VisionCall | None = None) -> ClassificationDecision:
    evidence = inspect_text_layers(pdf_path)  # every page, Arabic/Latin letters only
    if evidence.has_latin_body and not evidence.has_arabic_body:
        return english_decision(evidence)

    call = vision_call or call_local_router
    screen_votes = call_in_batches(
        render_all_page_thumbnails(pdf_path),
        settings.arabic_classifier_batch_pages,
        call,
        "page_screen",
    )
    detailed = call(render_suspicious_pages(pdf_path, screen_votes), "detail")
    votes = merge_votes(screen_votes, detailed)
    return aggregate_votes(evidence, votes)
```

`call_local_router` must POST strict JSON to
`{ARABIC_ROUTER_BASE_URL}/api/chat` with model `ARABIC_ROUTER_MODEL`, no cloud
fallback, no Gemini key, temperature `0`, and a finite timeout. Reject unknown
enum values, missing pages, non-numeric confidence, and JSON outside the one
expected object/array. A confident handwritten decision requires all primary-
content evidence to be non-printed and aggregate confidence at or above
`ARABIC_HANDWRITTEN_CONFIDENCE_MIN`; otherwise abstain.

- [x] **Step 5: Pass classifier tests**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_classifier.py -q
```

Expected: PASS, including the five Review Focus cases owned by this task.

- [x] **Step 6: Commit the classifier**

```bash
git add backend/app/extraction/arabic_types.py \
  backend/app/extraction/arabic_classifier.py backend/tests/test_arabic_classifier.py
git commit -m "feat: classify Arabic documents locally"
```

---

### Task 4: Implement the Gemini 3.7 Flash key cascade

**Files:**
- Create: `backend/app/extraction/gemini_ocr_client.py`
- Extend: `backend/app/extraction/arabic_types.py`
- Test: `backend/tests/test_gemini_ocr_client.py`

**Interfaces:**
- Consumes: `settings.gemini_api_keys`, rendered PNG bytes, absolute 1-based
  page numbers, and a response validator supplied by the orchestrator.
- Produces:
  `GeminiOcrClient.generate_batch(pages, validator) -> OcrBatchResult` and
  `GeminiKeysExhausted` with a categorized final cause and the best valid
  partial response, if one was returned.

- [ ] **Step 1: Write failure-policy and request-shape tests**

```python
# backend/tests/test_gemini_ocr_client.py
def test_429_rotates_to_next_key(fake_clients, caplog):
    fake_clients["k1"].raise_code = 429
    fake_clients["k2"].text = "<!-- PAGE:1 -->\nمرحبا\n<!-- END_PAGE:1 -->"
    result = make_client("k1,k2", fake_clients).generate_batch(
        [page(1)], validator=complete_validator
    )
    assert result.key_index == 1
    assert "k1" not in caplog.text and "k2" not in caplog.text


def test_bad_request_does_not_rotate(fake_clients):
    fake_clients["k1"].raise_code = 400
    with pytest.raises(GeminiRequestInvalid):
        make_client("k1,k2", fake_clients).generate_batch(
            [page(1)], validator=complete_validator
        )
    assert fake_clients["k2"].calls == 0


def test_request_uses_low_thinking_and_high_media_resolution(fake_clients):
    make_client("k1", fake_clients).generate_batch(
        [page(1)], validator=complete_validator
    )
    config = fake_clients["k1"].last_config
    assert config.thinking_config.thinking_level == "low"
    assert config.media_resolution.name == "MEDIA_RESOLUTION_HIGH"
```

Add cases for 401/403 rotation, one bounded retry on empty/invalid/truncated
output through an injected validator,
408/network/5xx retry then rotation, usage metadata including
`thoughts_token_count`, no keys configured, and all keys exhausted.

- [ ] **Step 2: Run and verify failure**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_gemini_ocr_client.py -q
```

Expected: FAIL because the client does not exist.

- [ ] **Step 3: Add typed request/result/failure models**

```python
# backend/app/extraction/arabic_types.py
@dataclass(frozen=True)
class RenderedPage:
    page_number: int
    png: bytes

@dataclass(frozen=True)
class OcrUsage:
    prompt_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    total_tokens: int = 0

@dataclass(frozen=True)
class OcrBatchResult:
    text: str
    provider: str
    model: str
    key_index: int | None
    usage: OcrUsage
    latency_ms: int
```

Define `GeminiRequestInvalid`, `GeminiOutputInvalid`, and
`GeminiKeysExhausted(final_kind: str, best_partial: OcrBatchResult | None,
attempt_usage: tuple[OcrUsage, ...])` without storing key material. The
validator returns how many contiguous requested pages are complete. A partial
response is not success, but the longest partial response is retained in the
final exception so Task 7 can preserve it if all keys fail.

- [ ] **Step 4: Implement the official SDK request and cascade**

```python
# backend/app/extraction/gemini_ocr_client.py
config = types.GenerateContentConfig(
    max_output_tokens=settings.arabic_ocr_max_output_tokens,
    media_resolution=getattr(
        types.MediaResolution,
        f"MEDIA_RESOLUTION_{settings.arabic_gemini_media_resolution}",
    ),
    thinking_config=types.ThinkingConfig(
        thinking_level=settings.arabic_gemini_printed_thinking_level
    ),
)

contents = [types.Part.from_text(text=batch_prompt(pages))]
for page in pages:
    contents.extend([
        types.Part.from_text(text=f"PAGE {page.page_number}"),
        types.Part.from_bytes(data=page.png, mime_type="image/png"),
    ])
response = client.models.generate_content(
    model=settings.arabic_gemini_printed_model,
    contents=contents,
    config=config,
)
```

Create one `genai.Client(api_key=key)` per attempt through an injectable
factory. Catch `google.genai.errors.APIError` and branch on `e.code`: 400 fails
immediately; 401/403 rotate; 429 optionally honors one short `Retry-After` then
rotates; 408/5xx retry with bounded jitter then rotate. Capture
`prompt_token_count`, `candidates_token_count`, `thoughts_token_count`, and
`total_token_count`. Log only `key_index`, model, page range, failure kind,
latency, and token counts.

- [ ] **Step 5: Pass the client tests**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_gemini_ocr_client.py -q
```

Expected: PASS and captured logs contain no test key values.

- [ ] **Step 6: Commit the Gemini client**

```bash
git add backend/app/extraction/arabic_types.py \
  backend/app/extraction/gemini_ocr_client.py \
  backend/tests/test_gemini_ocr_client.py
git commit -m "feat: add Gemini Arabic OCR key cascade"
```

---

### Task 5: Parse page-bounded Markdown into the existing artifact contract

**Files:**
- Create: `backend/app/extraction/arabic_adapter.py`
- Extend: `backend/app/extraction/arabic_types.py`
- Test: `backend/tests/test_arabic_adapter.py`

**Interfaces:**
- Consumes: one raw model response, the requested absolute page numbers, and
  provider/model identifiers.
- Produces:
  `parse_complete_page_prefix(...) -> ParsedPagePrefix` and
  `pages_to_content_list(pages: Sequence[ArabicOcrPage]) -> list[dict]`.

- [ ] **Step 1: Write failing boundary and structure tests**

```python
# backend/tests/test_arabic_adapter.py
def test_complete_prefix_survives_truncated_last_page():
    raw = """<!-- PAGE:5 -->\n# عنوان\nنص\n<!-- END_PAGE:5 -->
<!-- PAGE:6 -->\nنص غير مكتمل"""
    parsed = parse_complete_page_prefix(
        raw, expected_pages=[5, 6], provider="gemini_arabic_flash", model="m"
    )
    assert [p.page_number for p in parsed.pages] == [5]
    assert parsed.first_uncommitted_page == 6
    assert parsed.is_complete is False


def test_gap_or_duplicate_never_commits_later_pages():
    raw = page_section(1, "أ") + page_section(3, "ج")
    parsed = parse_complete_page_prefix(raw, [1, 2, 3], "gemini", "m")
    assert [p.page_number for p in parsed.pages] == [1]
    assert parsed.first_uncommitted_page == 2


def test_markdown_maps_to_page_indexed_content_list():
    pages = [ocr_page(2, "# عنوان\n\n- أول\n- ثان\n\n| أ | ب |\n|---|---|\n| ١ | ٢ |")]
    blocks = pages_to_content_list(pages)
    assert {b["type"] for b in blocks} >= {"text", "list", "table"}
    assert {b["page_idx"] for b in blocks} == {1}
    assert all(b["ocr_provider"] == pages[0].provider for b in blocks)
```

Add cases for missing start/end markers, out-of-order pages, repeated response
loops, empty pages, HTML tables, fenced equations, captions, and ambiguous
Markdown preserved as `type="text"` rather than dropped.

- [ ] **Step 2: Run and verify failure**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_adapter.py -q
```

Expected: FAIL because the adapter does not exist.

- [ ] **Step 3: Add page-level parsing types**

```python
# backend/app/extraction/arabic_types.py
@dataclass(frozen=True)
class ArabicOcrPage:
    page_number: int
    raw_markdown: str
    markdown: str
    provider: str
    model: str
    repaired: bool = False

@dataclass(frozen=True)
class ParsedPagePrefix:
    pages: tuple[ArabicOcrPage, ...]
    first_uncommitted_page: int | None
    is_complete: bool
```

- [ ] **Step 4: Implement strict contiguous-prefix parsing**

Use only these exact sentinels in model prompts and parsing:

```text
<!-- PAGE:{absolute_1_based_page} -->
...structured Markdown...
<!-- END_PAGE:{absolute_1_based_page} -->
```

`parse_complete_page_prefix` must scan in request order and stop at the first
missing, duplicated, empty, malformed, or mismatched page. It may return valid
pages before that point, but must never accept a later page across a gap. Detect
large adjacent repetition loops and treat that page as incomplete; do not
silently delete text.

- [ ] **Step 5: Implement deterministic Markdown conversion**

Map headings to `{"type":"text","text_level":N,"text":...}`, lists to
`{"type":"list","list_items":[...]}`, Markdown/HTML tables to
`{"type":"table","table_body":...}`, fenced display math to
`{"type":"equation","text":...}`, and remaining paragraphs/captions to
`{"type":"text","text":...}`. Every entry carries zero-based `page_idx`
plus the non-secret extension fields `ocr_provider`, `ocr_model`, and
`ocr_repaired`. Keep those fields in the artifact for block provenance; the
existing chunker safely ignores unknown keys.

- [ ] **Step 6: Pass the adapter and chunker compatibility tests**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_adapter.py \
  tests/test_chunker_algorithm_and_broken_tables.py \
  tests/test_chunker_equations.py tests/test_chunker_tables.py -q
```

Expected: PASS. The test must write the generated list to a temporary
`content_list.json` and prove `create_chunks_from_content_list` returns ordered
1-based page numbers.

- [ ] **Step 7: Commit the adapter**

```bash
git add backend/app/extraction/arabic_types.py \
  backend/app/extraction/arabic_adapter.py backend/tests/test_arabic_adapter.py
git commit -m "feat: adapt Arabic OCR to structural artifacts"
```

---

### Task 6: Add the printed-only Gemma fallback and scoped repair

**Files:**
- Create: `backend/app/extraction/arabic_fallback.py`
- Create: `backend/app/extraction/arabic_repair.py`
- Test: `backend/tests/test_arabic_repair.py`
- Test: `backend/tests/test_arabic_ocr_fallback.py`

**Interfaces:**
- Consumes: remaining `RenderedPage` objects and the existing comma-separated
  `OLLAMA_API_KEY` setting.
- Produces:
  `GemmaArabicFallback.generate_page(page) -> OcrBatchResult` and
  `repair_gemma_page(page, source_text) -> ArabicOcrPage`.

- [ ] **Step 1: Write failing provider-isolation tests**

```python
# backend/tests/test_arabic_ocr_fallback.py
def test_fallback_rotates_only_ollama_keys(fake_http):
    fake_http.key("o1").returns(429)
    fake_http.key("o2").returns(200, ollama_page(7, "نص"))
    result = make_fallback("o1,o2", fake_http).generate_page(page(7))
    assert result.provider == "gemma4_arabic_fallback"
    assert fake_http.keys_called == ["o1", "o2"]


def test_fallback_rejects_handwritten_input(fake_http):
    with pytest.raises(HandwrittenFallbackForbidden):
        make_fallback("o1", fake_http).generate_page(page(1), writing_style="handwritten")
    assert fake_http.calls == 0
```

Test that the fallback talks directly to the configured Ollama `/api/chat`
endpoint, sends only `gemma4:31b-cloud`, uses temperature `0`, includes the
single page image and absolute page marker, and never falls through to any
OpenAI/Anthropic/NVIDIA provider in the generic chat cascade.

- [ ] **Step 2: Write failing repair-scope tests**

```python
# backend/tests/test_arabic_repair.py
def test_gemini_page_is_returned_byte_for_byte():
    original = ocr_page(1, "إِنَّ النص\u200f", provider="gemini_arabic_flash")
    assert repair_gemma_page(original, source_text="") is original


def test_gemma_cleanup_is_canonical_not_orthographic():
    original = ocr_page(1, "ﺍﻟﻨﺺ\x00", provider="gemma4_arabic_fallback")
    repaired = repair_gemma_page(original, source_text="")
    assert repaired.markdown == "النص"
    assert repaired.repaired is True
```

Add explicit regressions proving the repair does **not** normalize Alef
variants, remove tashkeel, swap ta-marbuta/ha, swap ya/alef-maqsura, or delete
repeated prose without source evidence.

- [ ] **Step 3: Run and verify failure**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_repair.py \
  tests/test_arabic_ocr_fallback.py -q
```

Expected: FAIL because both modules are missing.

- [ ] **Step 4: Implement the dedicated Ollama key cascade**

Construct the `/api/chat` request in `arabic_fallback.py` rather than calling
the generic provider cascade. Iterate `settings.ollama_api_keys` (or one
keyless local target), attach the PNG as base64 to the final user message, and
validate the returned page with Task 5's parser. Rotate on authentication,
quota, timeout, network, and 5xx failures; reject request-shape errors. Do not
log authorization headers, bodies, OCR text, or key values.

- [ ] **Step 5: Implement conservative Gemma-only repair**

`repair_gemma_page` must return non-Gemma pages by identity before any
normalization. For Gemma pages, preserve `raw_markdown`, normalize Arabic
presentation forms with Unicode NFKC, remove NUL/unsafe control bytes and
duplicated zero-width marks, and set `repaired` only when text changes. Use a
usable PDF Arabic text layer only for an exact, high-confidence span alignment;
otherwise leave ambiguous OCR unchanged. A detected repetition loop raises
`GemmaOutputInvalid` for retry instead of deleting the loop.

- [ ] **Step 6: Pass fallback and repair tests**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_repair.py \
  tests/test_arabic_ocr_fallback.py -q
```

Expected: PASS, including byte-for-byte Gemini preservation.

- [ ] **Step 7: Commit the fallback**

```bash
git add backend/app/extraction/arabic_fallback.py \
  backend/app/extraction/arabic_repair.py \
  backend/tests/test_arabic_repair.py \
  backend/tests/test_arabic_ocr_fallback.py
git commit -m "feat: add scoped Gemma Arabic OCR fallback"
```

---

### Task 7: Orchestrate adaptive batches and page-resumable fallback

**Files:**
- Create: `backend/app/extraction/arabic_ocr.py`
- Extend: `backend/app/extraction/arabic_types.py`
- Test: `backend/tests/test_arabic_ocr_fallback.py`

**Interfaces:**
- Consumes: a printed-Arabic `ClassificationDecision`, PDF path, output path,
  Gemini/Gemma clients, and an optional progress callback.
- Produces:
  `extract_arabic_document(...) -> ArabicExtractionResult`, an atomically
  published `content_list.json`, `document.md`, `raw_pages.json`, and
  `ocr_manifest.json`.

- [ ] **Step 1: Add failing continuation and atomicity tests**

```python
# backend/tests/test_arabic_ocr_fallback.py
def test_truncated_batch_keeps_complete_gemini_prefix(tmp_path, four_page_pdf):
    partial = ocr_batch(
        page_section(1, "واحد") + page_section(2, "اثنان")
        + "<!-- PAGE:3 -->\nمبتور"
    )
    gemini = fake_gemini(raises=GeminiKeysExhausted(
        "daily_quota", best_partial=partial, attempt_usage=(partial.usage,)
    ))
    gemma = fake_gemma({3: "ثلاثة", 4: "أربعة"})
    result = extract(four_page_pdf, tmp_path / "out", gemini, gemma)
    assert result.provider_summary == [
        {"provider": "gemini_arabic_flash", "pages": [1, 2]},
        {"provider": "gemma4_arabic_fallback", "pages": [3, 4]},
    ]
    assert gemma.requested_pages == [3, 4]


def test_failed_final_page_never_publishes_partial_output(tmp_path, two_page_pdf):
    target = tmp_path / "out"
    with pytest.raises(ArabicExtractionFailed):
        extract(two_page_pdf, target, exhausted_gemini(), broken_gemma())
    assert not target.exists()
```

Add cases for one request at or below the page threshold, four-page batching
above it, gap/duplicate rejection, progress values, RGB rendering at 200 DPI,
no provider split inside a page, retry of an entirely invalid Gemini response,
and rollback when replacing a pre-existing artifact directory fails.

- [ ] **Step 2: Run and verify failure**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_ocr_fallback.py -q
```

Expected: FAIL on missing orchestrator behavior.

- [ ] **Step 3: Add the result contract**

```python
# backend/app/extraction/arabic_types.py
@dataclass(frozen=True)
class ArabicExtractionResult:
    output_dir: Path
    extractor: str
    pages: tuple[ArabicOcrPage, ...]
    provider_summary: tuple[dict, ...]
    usage: OcrUsage
```

- [ ] **Step 4: Implement rendering and batching**

Open the PDF with PyMuPDF, render every page to PNG in RGB at
`ARABIC_OCR_DPI`, and retain absolute 1-based page numbers. Use one batch when
the document page count is at or below
`ARABIC_OCR_SINGLE_REQUEST_MAX_PAGES`; otherwise use consecutive batches of
`ARABIC_OCR_BATCH_PAGES`. Never binarize, grayscale, sharpen, or change
contrast.

- [ ] **Step 5: Implement the page commit cursor**

Maintain `next_page = 1` and an in-memory ordered page map. Supply a validator
backed by `parse_complete_page_prefix` to the Gemini client. For each complete
response, append its contiguous pages and advance `next_page`. If the client
raises `GeminiKeysExhausted` with `best_partial`, parse and append only that
partial response's contiguous valid prefix. Then send every page from the new
`next_page` through the printed-only Gemma fallback. Repair each Gemma page
before adapting it. Validate the final keys are exactly `1..page_count`.

Gemini client failures classified as request/schema errors remain fatal; key
rotation cannot fix them. Authentication, permission, quota, timeout, network,
service, empty-output, and incomplete-output exhaustion may activate Gemma.

- [ ] **Step 6: Publish artifacts atomically**

Write all artifacts under `output_dir.parent / f".{output_dir.name}.staging-{uuid4()}"`.
`raw_pages.json` retains both raw and repaired Gemma text plus non-secret
provenance; Gemini raw and final text are identical. `ocr_manifest.json`
contains classification, page ranges, models, retry counts, latency, and token
usage, but no keys or OCR prompt. Promote the staging directory with same-
filesystem renames. If `output_dir` exists, rename it to an attempt-specific
backup, promote staging, delete the backup only after success, and restore it
if promotion fails.

- [ ] **Step 7: Pass orchestrator tests**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_ocr_fallback.py \
  tests/test_arabic_adapter.py tests/test_gemini_ocr_client.py -q
```

Expected: PASS. Inspect the test artifact and confirm all page indices are
contiguous and every Gemma block alone has `ocr_repaired=true` when changed.

- [ ] **Step 8: Commit the orchestrator**

```bash
git add backend/app/extraction/arabic_types.py \
  backend/app/extraction/arabic_ocr.py \
  backend/tests/test_arabic_ocr_fallback.py
git commit -m "feat: orchestrate resumable Arabic OCR"
```

---

### Task 8: Integrate routing without changing the English pipeline

**Files:**
- Modify: `backend/app/extraction/pipeline_sync.py:38-120`
- Modify: `backend/app/extraction/pipeline_sync.py:276-390`
- Modify: `backend/app/extraction/pipeline_sync.py:582-610`
- Extend: `backend/app/extraction/arabic_types.py`
- Test: `backend/tests/test_arabic_pipeline_routing.py`

**Interfaces:**
- Consumes: Task 3 classification and Task 7 extraction.
- Produces: typed `handwritten_arabic_unavailable` and
  `arabic_style_confirmation_required` failures, persisted routing metadata,
  and exact preservation of the pre-existing English branch.

- [ ] **Step 1: Write failing route-isolation tests**

```python
# backend/tests/test_arabic_pipeline_routing.py
def test_feature_off_never_calls_classifier(existing_pipeline, mocks):
    run_pipeline_sync(**existing_pipeline)
    mocks.classifier.assert_not_called()
    mocks.resolve_extractor.assert_called_once()


def test_english_route_calls_existing_resolver_only(arabic_enabled_pipeline, mocks):
    mocks.classifier.return_value = english_decision()
    run_pipeline_sync(**arabic_enabled_pipeline)
    mocks.resolve_extractor.assert_called_once()
    mocks.arabic_extractor.assert_not_called()


def test_disabled_handwriting_calls_no_ocr_and_keeps_source(handwritten_pipeline, mocks):
    with pytest.raises(HandwrittenArabicUnavailable):
        run_pipeline_sync(**handwritten_pipeline)
    mocks.gemini.assert_not_called()
    mocks.gemma.assert_not_called()
    assert handwritten_pipeline["pdf_path"].exists()
    assert count_chunks(handwritten_pipeline["document_id"]) == 0
```

Add tests for printed Arabic, mixed Arabic/English, uncertain style, manual
classification reuse, provider summary/extractor persistence, and proving
`repair_chunks` is called for English but never for an Arabic extractor. Add a
Gemini regression containing Alef variants, tashkeel, ta-marbuta, ya, and
alef-maqsura; assert the final Arabic chunks preserve those code points exactly
apart from the Markdown structure removed by the adapter.

- [ ] **Step 2: Run and verify failure**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_pipeline_routing.py -q
```

Expected: FAIL because routing is not integrated.

- [ ] **Step 3: Add typed user-visible failures**

```python
# backend/app/extraction/arabic_types.py
class ArabicPipelineError(RuntimeError):
    error_code: str
    public_message: str

class HandwrittenArabicUnavailable(ArabicPipelineError):
    error_code = "handwritten_arabic_unavailable"
    public_message = (
        "Handwritten Arabic extraction is not currently available because it "
        "requires Gemini Pro with a billing-enabled account. No text was "
        "extracted, and your original file has been kept."
    )

class ArabicStyleConfirmationRequired(ArabicPipelineError):
    error_code = "arabic_style_confirmation_required"
    public_message = "Confirm whether this Arabic document is printed or handwritten."
```

- [ ] **Step 4: Branch immediately before the existing resolver**

When `ARABIC_OCR_ENABLED=false`, execute the current `resolve_extractor` call
without classifying. When enabled, reuse a `classification_source=user_confirmed`
decision if present; otherwise classify and persist all decision fields.

- English: call the existing `resolve_extractor` unchanged.
- Printed or mixed printed Arabic: call `extract_arabic_document` and persist
  `extractor` plus `ocr_provider_summary`.
- Confident handwritten with the handwritten flag false: raise
  `HandwrittenArabicUnavailable` before constructing either OCR client.
- Uncertain: raise `ArabicStyleConfirmationRequired` before any OCR call.

Reserve a disabled branch for `gemini-3.1-pro-preview`; it must raise an
explicit configuration error rather than make a request until a later,
billing-enabled implementation is approved.

- [ ] **Step 5: Keep repair paths mutually exclusive**

The Arabic orchestrator has already repaired only Gemma pages. Guard the
existing `glyph_repair.repair_chunks` call so it runs only for non-Arabic
extractors. Do not run it on Gemini-only, Gemma-only, or hybrid Arabic chunks.
All later chunking, assets, embedding, summarization, and completion code stays
shared and unchanged.

- [ ] **Step 6: Persist typed failures safely**

Teach `_sanitize_error_for_user` and `_handle_ingestion_failure` to use
`ArabicPipelineError.public_message` and `.error_code`. The failure cleanup
removes database chunks but never the uploaded PDF. It must not include raw
provider errors, model output, local paths, or keys in API-visible fields.

- [ ] **Step 7: Pass routing and existing extraction tests**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_pipeline_routing.py \
  tests/test_ingestion_pipeline.py tests/test_vlm_extractor.py \
  tests/test_chunker_algorithm_and_broken_tables.py \
  tests/test_chunker_equations.py tests/test_chunker_tables.py \
  tests/test_glyph_repair_dropped_f.py -q
```

Expected: PASS. English golden chunk payloads remain byte-equivalent apart
from newly nullable document metadata.

- [ ] **Step 8: Commit routing integration**

```bash
git add backend/app/extraction/arabic_types.py \
  backend/app/extraction/pipeline_sync.py \
  backend/tests/test_arabic_pipeline_routing.py
git commit -m "feat: route Arabic documents before extraction"
```

---

### Task 9: Add manual confirmation without creating a new job record

**Files:**
- Modify: `backend/app/schemas/documents.py`
- Modify: `backend/app/api/v1/endpoints/documents.py`
- Modify: `backend/app/services/ingestion.py`
- Test: `backend/tests/test_arabic_confirmation_api.py`

**Interfaces:**
- Consumes: the latest failed job with
  `error_code=arabic_style_confirmation_required`.
- Produces:
  `POST /api/v1/papers/{paper_id}/arabic-writing-style` with body
  `{"writing_style":"printed"|"handwritten"}`.

- [ ] **Step 1: Write failing endpoint tests**

```python
# backend/tests/test_arabic_confirmation_api.py
async def test_confirm_printed_requeues_same_job(client, uncertain_document):
    before = uncertain_document.job_id
    response = await client.post(
        f"/api/v1/papers/{uncertain_document.id}/arabic-writing-style",
        json={"writing_style": "printed"},
    )
    assert response.status_code == 202
    assert response.json()["job_id"] == str(before)
    assert latest_job(before)["status"] == "queued"
    assert document(uncertain_document.id)["classification_source"] == "user_confirmed"


async def test_confirm_handwritten_does_not_dispatch(client, uncertain_document, celery):
    response = await client.post(
        f"/api/v1/papers/{uncertain_document.id}/arabic-writing-style",
        json={"writing_style": "handwritten"},
    )
    assert response.status_code == 200
    assert response.json()["error_code"] == "handwritten_arabic_unavailable"
    celery.delay.assert_not_called()
```

Add owner-isolation, invalid-choice, non-uncertain job, queue-full rollback,
and dispatch-failure tests.

- [ ] **Step 2: Run and verify failure**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_confirmation_api.py -q
```

Expected: FAIL with a missing route.

- [ ] **Step 3: Add the request schema and transactional endpoint**

```python
class ArabicWritingStyleConfirmation(BaseModel):
    writing_style: Literal["printed", "handwritten"]
```

Lock the owned document and its latest job. Accept only the typed confirmation
failure. For `printed`, persist Arabic/printed/RTL with
`classification_source=user_confirmed`, call `requeue_failed_job` on the same
row, commit, and dispatch `process_ingestion`. For `handwritten`, persist the
manual decision and update the existing job/document to the unsupported typed
failure without reserving capacity or dispatching work.

If Celery dispatch fails after the printed commit, restore the job/document to
a typed failed state exactly as the existing re-extract endpoint does.

- [ ] **Step 4: Pass confirmation and queue-capacity tests**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_confirmation_api.py \
  tests/test_capacity.py -q
```

Expected: PASS and no duplicate ingestion job exists.

- [ ] **Step 5: Commit the confirmation flow**

```bash
git add backend/app/schemas/documents.py \
  backend/app/api/v1/endpoints/documents.py \
  backend/app/services/ingestion.py \
  backend/tests/test_arabic_confirmation_api.py
git commit -m "feat: confirm uncertain Arabic writing style"
```

---

### Task 10: Expose stable Arabic status and reader metadata

**Files:**
- Modify: `backend/app/api/v1/endpoints/documents.py:500-570`
- Modify: `backend/app/api/v1/endpoints/chunks.py:100-180`
- Modify: `backend/app/database/repositories/documents.py`
- Modify: `backend/app/schemas/documents.py`
- Test: `backend/tests/test_arabic_document_api.py`

**Interfaces:**
- Produces optional status fields:
  `error_code`, `error_message`, `action_required`, `allowed_actions`,
  classification fields, `text_direction`, and `ocr_provider_summary`.

- [ ] **Step 1: Write failing API contract tests**

```python
# backend/tests/test_arabic_document_api.py
async def test_progress_exposes_confirmation_action(client, uncertain_document):
    body = (await client.get(f"/api/v1/papers/{uncertain_document.id}/progress")).json()
    assert body["error_code"] == "arabic_style_confirmation_required"
    assert body["action_required"] == "confirm_arabic_writing_style"
    assert body["allowed_actions"] == ["printed", "handwritten"]


async def test_full_document_exposes_rtl_and_no_secret(client, hybrid_document):
    body = (await client.get(f"/api/v1/papers/{hybrid_document.id}/document")).json()
    assert body["text_direction"] == "rtl"
    assert body["detected_language"] == "mixed"
    assert "api_key" not in json.dumps(body).lower()
```

Also assert English documents return `text_direction="ltr"` and old rows with
null metadata serialize without error.

- [ ] **Step 2: Run and verify failure**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_document_api.py -q
```

Expected: FAIL on absent fields.

- [ ] **Step 3: Extend progress, list/detail, and full-document responses**

Select `error_code` and `error_message` from the latest job instead of parsing
human-readable text. Derive `action_required` and `allowed_actions` only from
the typed code. Return document classification/direction/provider summary from
metadata. The full-document endpoint returns the same direction fields beside
`blocks`; it does not copy credentials or full provider responses.

- [ ] **Step 4: Pass focused and existing endpoint tests**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_document_api.py \
  tests/test_ownership.py tests/test_chunks_multi_document.py \
  tests/test_chunk_sequence.py tests/test_strict_document_scope.py -q
```

Expected: PASS with backward-compatible optional additions.

- [ ] **Step 5: Commit the API contract**

```bash
git add backend/app/api/v1/endpoints/documents.py \
  backend/app/api/v1/endpoints/chunks.py \
  backend/app/database/repositories/documents.py \
  backend/app/schemas/documents.py backend/tests/test_arabic_document_api.py
git commit -m "feat: expose Arabic OCR document status"
```

---

### Task 11: Render persistent status and block-level direction in React

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/views/ProcessingOverlay.tsx`
- Modify: `frontend/src/views/LibraryView.tsx`
- Modify: `frontend/src/views/ArticleReader.tsx`
- Modify: `frontend/src/views/ArticleBlock.tsx`
- Modify: `frontend/src/index.css`
- Create: `frontend/src/lib/documentDirection.ts`
- Create: `frontend/src/lib/documentDirection.test.ts`
- Create: `frontend/src/views/ProcessingOverlay.test.tsx`
- Create: `frontend/src/views/ArticleBlock.test.tsx`
- Create: `frontend/src/api.arabic.test.ts`
- Create: `frontend/src/components/ArabicOcrStatus.tsx`
- Create: `frontend/src/components/ArabicOcrStatus.test.tsx`
- Create: `frontend/src/test/setup.ts`

**Interfaces:**
- Consumes: Task 10's optional response fields and Task 9's confirmation
  endpoint.
- Produces: persistent unsupported/confirmation UI and correct document-content
  direction without changing application chrome.

- [x] **Step 1: Add the test runner**

```bash
cd frontend
npm install --save-dev vitest jsdom @testing-library/react \
  @testing-library/jest-dom @testing-library/user-event
```

Add `"test": "vitest"` to scripts, `test.environment="jsdom"` and
`setupFiles=["./src/test/setup.ts"]` to Vite config, and import
`@testing-library/jest-dom/vitest` in the setup file.

- [x] **Step 2: Write failing direction and status tests**

```typescript
// frontend/src/lib/documentDirection.test.ts
expect(documentDirection("english")).toBe("ltr")
expect(documentDirection("arabic")).toBe("rtl")
expect(documentDirection("mixed")).toBe("rtl")
expect(blockDirection("rtl", "English heading only")).toBe("auto")
expect(blockDirection("rtl", "العنوان 2026")).toBe("rtl")
```

In `ProcessingOverlay.test.tsx`, assert the exact handwritten-unavailable
message remains visible for a failed job and that an uncertain job exposes
printed/handwritten buttons whose callbacks receive the chosen value.

- [x] **Step 3: Run and verify failure**

```bash
cd frontend
npm test -- --run src/lib/documentDirection.test.ts \
  src/views/ProcessingOverlay.test.tsx
```

Expected: FAIL because the helpers and UI do not exist.

- [x] **Step 4: Extend typed API clients**

Add optional classification/direction/provider/error/action fields to
`PaperMeta`, `ProgressResponse`, and `FullDocument`. Add
`confirmArabicWritingStyle(paperId, writingStyle)` using the Task 9 endpoint.
Keep all fields optional so cached/older server responses continue rendering.

- [x] **Step 5: Implement persistent status handling**

`App.tsx` must stop polling terminal failed jobs but keep their typed status in
state. `ProcessingOverlay` displays the handwritten message as a blocking
panel, not a toast. For confirmation-required jobs it renders both actions,
disables them while submitting, resumes polling after printed confirmation,
and switches to the handwritten unavailable panel after handwritten
confirmation. `LibraryView` shows a persistent status badge/action on the row.

- [x] **Step 6: Apply direction only to document content**

Set a reader content wrapper to the document base direction. Pass it to each
`ArticleBlock`; set `dir="rtl"` when an RTL document block contains Arabic
letters, `dir="auto"` for Latin-only blocks inside an RTL document, and
`dir="ltr"` for English documents. Use CSS logical properties and
`text-align:start`; give RTL lists/tables the correct flow. Do not put `dir`
on the app root, navigation, controls, side rails, or modals.

- [x] **Step 7: Pass frontend tests and production build**

```bash
cd frontend
npm test -- --run
npm run build
```

Expected: all tests pass and TypeScript/Vite complete without warnings caused
by this change.

- [x] **Step 8: Commit the frontend**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts \
  frontend/src/api.ts frontend/src/App.tsx \
  frontend/src/types.ts frontend/src/api.arabic.test.ts \
  frontend/src/views/ProcessingOverlay.tsx frontend/src/views/LibraryView.tsx \
  frontend/src/views/ArticleReader.tsx frontend/src/views/ArticleBlock.tsx \
  frontend/src/views/ArticleBlock.test.tsx \
  frontend/src/components/ArabicOcrStatus.tsx \
  frontend/src/components/ArabicOcrStatus.test.tsx \
  frontend/src/index.css frontend/src/lib/documentDirection.ts \
  frontend/src/lib/documentDirection.test.ts \
  frontend/src/views/ProcessingOverlay.test.tsx frontend/src/test/setup.ts
git commit -m "feat: show Arabic OCR state and direction"
```

---

### Task 12: Build the frozen sandbox evaluation harness

**Files:**
- Create: `backend/eval/__init__.py`
- Create: `backend/eval/arabic_documents/__init__.py`
- Create: `backend/eval/arabic_documents/run_eval.py`
- Create: `backend/eval/arabic_documents/scoring.py`
- Create: `backend/eval/arabic_documents/README.md`
- Create: `backend/eval/arabic_documents/manifest.example.json`
- Modify: `backend/.gitignore`
- Test: `backend/tests/test_arabic_scoring.py`

**Interfaces:**
- Consumes: a manifest of local PDF/ground-truth paths outside git and the
  production classifier/extractor modules.
- Produces: immutable JSONL records and a Markdown/JSON report with split
  metrics, routing confusion, costs, latency, and paired provider results.

- [x] **Step 1: Write failing deterministic scoring tests**

```python
# backend/tests/test_arabic_scoring.py
def test_eval_normalization_never_changes_stored_text():
    raw = "إِنَّ الـنص"
    assert normalize_for_score(raw) == "ان النص"
    assert raw == "إِنَّ الـنص"


def test_classifier_report_counts_abstention_separately():
    report = score_routes([
        prediction("printed", "printed"),
        prediction("handwritten", "uncertain"),
    ])
    assert report["coverage"] == 0.5
    assert report["printed_as_handwritten"] == 0
    assert report["handwritten_as_printed"] == 0
```

Add exact CER, WER, token-F1, micro/per-document aggregation, cost including
thought tokens, p50/p95 latency, page gap/duplicate, and provider-range tests.

- [x] **Step 2: Run and verify failure**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_scoring.py -q
```

Expected: FAIL because scoring does not exist.

- [x] **Step 3: Implement offline scoring and immutable run records**

Normalization for metrics may remove harakat/tatweel, standardize configured
Alef/ya/ta-marbuta variants, strip Markdown, and collapse whitespace. It must
operate on copies and never feed normalized text back into artifacts. Each run
record includes a config hash, git SHA, route/ground truth, provider ranges,
request attempts, token categories including thoughts, latency, raw/normalized
scores, and no credentials or full prompts.

- [x] **Step 4: Implement safe preflight and live opt-in**

`run_eval.py` defaults to classifier-only/offline operation. Live OCR requires
an explicit `--live` flag and reads keys only from the environment. Before the
corpus run, send the smallest valid printed request to each Gemini key and
record only key index, accessible/unavailable, status category, model, token
counts, and latency. Never probe the disabled Pro model. Exit with an
actionable error if no key can access `gemini-3.7-flash`.

- [x] **Step 5: Document and freeze the corpus contract**

The README requires at least 10 digital printed Arabic, 10 scanned printed
Arabic, 10 handwritten, 5 mixed Arabic/English, and 5 English controls, plus
tables, numerals, signatures, handwriting-like fonts, multi-column pages, and
documents over four pages. The committed manifest is an example only; add the
real corpus directory and generated run directories to `.gitignore` because
documents may be private. Hash each source and ground-truth file so a reported
run cannot silently change its benchmark.

- [x] **Step 6: Pass scoring tests and run mocked end-to-end evaluation**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest tests/test_arabic_scoring.py -q
python -m eval.arabic_documents.run_eval \
  --manifest eval/arabic_documents/manifest.example.json --mock
```

Expected: tests pass and the mock run writes a report without network access.
Do not claim live accuracy until the real frozen corpus has been supplied and
run.

- [x] **Step 7: Commit the harness**

```bash
git add backend/eval/__init__.py backend/eval/arabic_documents \
  backend/.gitignore \
  backend/tests/test_arabic_scoring.py
git commit -m "test: add Arabic document OCR evaluation harness"
```

---

### Task 13: Verify in the sandbox, document operations, and open the PR

**Files:**
- Create: `docs/runbooks/arabic-document-ocr.md`
- Create: `docs/superpowers/plans/2026-09-25-arabic-document-ocr-pr.md`
- Modify only if evaluation requires frozen values:
  `backend/app/core/config.py`, `backend/.env.example`

**Interfaces:**
- Produces: verified feature-flagged branch, evidence report, operator runbook,
  and a pull request; does not deploy to the VPS.

- [x] **Step 1: Write the operator runbook before live testing**

Document required local Ollama model installation, memory/version preflight,
ignored `.env` variable names, safe enable/disable steps, classifier
confirmation flow, typed errors, Gemini/Gemma provider behavior, where
artifacts/reports live, rollback by feature flag, and the rule that handwritten
Pro remains disabled. Include no credential values. Also draft
`docs/superpowers/plans/2026-09-25-arabic-document-ocr-pr.md` with the PR
sections listed in Step 8; populate measured sections only after verification.

- [x] **Step 2: Run the complete automated suite**

```bash
cd backend
POSTGRES_DB=9xaipal_test pytest -q
cd ../frontend
npm test -- --run
npm run build
cd ..
docker compose -f backend/docker-compose.yml config >/dev/null
docker compose -f backend/docker-compose.prod.yml config >/dev/null
```

Expected: every command exits 0. If the full backend suite has a pre-existing
failure, record the exact command/output and prove all Arabic and directly
affected regression tests still pass; do not describe the suite as green.

Final verification after the `main` merge and all review fixes: full backend
suite 912 passed (340.52s); frontend suite 20 passed and the production build
(`tsc && vite build`) passed, both in `node:22-alpine`, with the existing
large-chunk advisory; both Compose configs passed (production config used a
throwaway value for required interpolation only); the synthetic `--mock`
evaluation exited 0. The test container used `DEBUG=true`.

- [ ] **Step 3: Run the real frozen sandbox corpus when supplied**

NOT RUN: the private frozen corpus/ground truth is absent from this workspace.
No live provider calls or accuracy claims were made; this remains a merge gate.

Place credentials only in ignored `backend/.env`, confirm with
`git check-ignore -v backend/.env`, then run:

```bash
cd backend
python -m eval.arabic_documents.run_eval \
  --manifest /absolute/path/to/frozen-arabic-documents/manifest.json --live
```

Do not run this step without the real corpus and human-verified ground truth.
Acceptance requires zero printed-to-handwritten and zero
handwritten-to-printed auto-routes on the held-out set; uncertain cases count
as abstentions. The chosen printed configuration must beat the Gemma-only
baseline, all successful documents must be page-complete, and cost must include
thought tokens. Report coverage separately; do not convert abstentions into
correct classifications.

- [ ] **Step 4: Perform visual browser checks against the sandbox**

NOT RUN: the approved PDFs and running browser sandbox were not supplied. Do
not treat synthetic tests as visual acceptance evidence.

Upload one English, printed Arabic, mixed, uncertain, and handwritten-control
PDF. Verify English chunks are unchanged/LTR; Arabic and mixed body content is
RTL; numerals and English spans remain readable; tables/lists/headings work in
light and dark themes; the uncertain action persists across reload; and the
handwritten message persists with zero chunks. Save screenshots in the
untracked evaluation run directory, not in the product tree.

- [x] **Step 5: Audit secrets, scope, and changes**

```bash
git diff --check main...HEAD
git status --short
git diff --name-only main...HEAD
if git grep -nE 'AQ\.[A-Za-z0-9_-]{20,}|GEMINI_API_KEYS=.+$' -- \
  backend frontend ':!backend/.env.example'; then
  echo 'Credential-like content found; stop and remove it.' >&2
  exit 1
fi
git diff main...HEAD -- backend/app/extraction/mineru_client.py \
  backend/app/extraction/vlm_client.py \
  backend/app/services/article_extraction.py backend/app/search
if git log -p main..HEAD | rg -n 'AQ\.[A-Za-z0-9_-]{20,}'; then
  echo 'Credential-like content found in branch history; stop and remove it.' >&2
  exit 1
fi
```

Expected: no whitespace errors, no untracked implementation files, no key
matches, and the final diff command is empty. `image.png` and the root
`pyproject.toml` in the original worktree remain untouched.

Verified: whitespace checks pass; code, runbook, PR draft, and branch-history
scans found no credential values; `backend/.env` is ignored; protected
MinerU/VLM/article-extraction/web-search paths have an empty diff. The only
match from an intentionally broader scan was this plan's literal credential-
detection regex, not a key. The original worktree's `image.png` and root
`pyproject.toml` remain unmodified by this branch.

- [x] **Step 6: Request code review and resolve findings**

Use `superpowers:requesting-code-review`, review every finding against the
approved spec, rerun the focused tests after fixes, then rerun Step 2. Do not
weaken the handwriting abstention or English-isolation tests to make them pass.

The initial review was incomplete. A later independent review identified
English MinerU isolation, classifier-failure, Gemma repair, Gemini retry,
prompt/adapter, persistence/API, classifier-policy, frontend, and evaluator
defects. Groups 1–8 were fixed in separate commits with test-first regression
coverage. In particular, clear English text-layer pages with embedded images
now bypass visual language screening; sparse scanned pages still reach it.
Follow-up fixes after the `main` merge: the classifier-outage English fallback
(which previously only covered documents that never needed the classifier),
a startup probe that no longer blocks on transient provider errors, MinerU
glyph/heading repair kept off Arabic OCR output at ingestion and re-chunk,
display-math prose handling, the Node 22 deploy build, and unchanged English
margin marks. Final full backend suite: 912 passed. The earlier 831-test run
is superseded. Live corpus accuracy remains unmeasured.

- [x] **Step 7: Commit operations documentation**

```bash
git add docs/runbooks/arabic-document-ocr.md \
  docs/superpowers/plans/2026-09-25-arabic-document-ocr-pr.md \
  backend/app/core/config.py backend/.env.example
git diff --cached --quiet || git commit -m "docs: add Arabic OCR operations runbook"
```

- [ ] **Step 8: Open the pull request without deploying**

```bash
git push -u origin feat/arabic-document-ocr
gh pr create \
  --base main \
  --head feat/arabic-document-ocr \
  --title "Add Arabic document OCR pipeline" \
  --body-file docs/superpowers/plans/2026-09-25-arabic-document-ocr-pr.md
```

Before this command, create the PR body file with: scope/non-goals, architecture,
test commands/results, classifier confusion and abstention metrics, OCR
CER/WER/F1 and cost/page, fallback proof, screenshots, known limitations, and
explicit statements that books/articles are deferred and handwritten Pro is
disabled. If the real corpus is not yet available, open the PR as a draft and
state that live acceptance is pending; do not invent metrics. In that case add
`--draft` to the `gh pr create` command above.

Expected: a reviewable PR exists. Merge/deploy only after human review; this
plan does not authorize a VPS deployment.

---

## External References Used by the Implementation

- Gemini 3.7 Flash model and thinking levels:
  <https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash> and
  <https://ai.google.dev/gemini-api/docs/thinking>
- Gemini image/media resolution:
  <https://ai.google.dev/gemini-api/docs/image-understanding>
- Gemini quota semantics:
  <https://ai.google.dev/gemini-api/docs/rate-limits>
- Gemini pricing/free-tier matrix:
  <https://ai.google.dev/gemini-api/docs/pricing>
- Official Google Gen AI Python SDK:
  <https://googleapis.github.io/python-genai/>
- W3C bidirectional text guidance:
  <https://www.w3.org/International/docs/bp-html-bidi/>
