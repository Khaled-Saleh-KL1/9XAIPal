# PDF Text Layer Quality Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tighten Arabic OCR fallback text-layer quality so header/footer-only content and visually reversed Arabic remain unreadable, while retaining useful body text such as QIMMA page 23.

**Architecture:** Keep the change in `arabic_ocr.py`'s existing `_usable_pdf_text_layer` gate. Count lexical body words after excluding page-number and recognizable running-furniture lines, then compare Arabic word plausibility in forward and reversed orientations using word-edge letter-position and morphology cues in addition to the existing small common-word check.

**Tech Stack:** Python, PyMuPDF, pytest in the repository's disposable Docker test environment.

**Spec:** `/private/tmp/claude-501/-Users-khaled-saleh-kl1-MyStuff-All-Programming-Files-9XAIPal-VPS/0df7da94-8918-4b81-8cdc-75713469848e/scratchpad/codex/spec-S2.md` and its parent `spec-S.md`.

## Global Constraints

- Stay on branch `fix/unreadable-page-text-layer`.
- Arabic route only — `backend/app/extraction/arabic_*.py`; MinerU/English untouched.
- Run focused tests ONLY in a throwaway container (no PYTHONPATH).
- Do NOT run the full suite.
- Never push/rebase/reset/clean/force, never ssh or contact production/external APIs, never restart containers.
- Do not pause for approval.
- Add each finding's input as a failing-then-passing test; keep all existing tests.

## Review Focus

- Three-line running furniture with few body words, including the review's exact Confidential/Annual Report/Page input; the unreadable marker remains. Task 1 adds this regression test.
- A short but real English or Arabic paragraph still qualifies through body-word count; the existing English and good-Arabic recovery tests cover it, and Task 1 reruns them.
- Reversed Arabic using words absent from `_COMMON_ARABIC_WORDS`; the review's exact nine-token input remains unreadable. Task 2 adds this regression test.
- Correctly ordered Arabic and the prior reversed-Arabic sample preserve their existing outcomes; Task 2 reruns both existing tests.
- QIMMA page 23's table-like text extraction remains accepted despite many short cell lines; Task 2 manually runs the same quality helper and records the result and first 200 characters in the report.

---

### Task 1: Require body words outside page furniture

**Files:**
- Modify: `backend/app/extraction/arabic_ocr.py`
- Test: `backend/tests/test_arabic_ocr_fallback.py`

**Interfaces:**
- Consumes: `_usable_pdf_text_layer(text: str, *, settings: Settings) -> str | None`.
- Produces: `_substantive_pdf_body_word_count(text: str) -> int`, used by the existing quality gate; page-number and recognizable running-furniture lines do not contribute to its count.

- [ ] **Step 1: Add the review's multi-line furniture input to the marker regression test.**

Add this exact case to `test_sparse_page_number_or_header_text_layer_keeps_unreadable_marker`:

```python
pytest.param(
    "Confidential Copy\nAnnual Report 2024\nPage 23 of 120",
    id="multi-line-header-footer-page-number",
)
```

- [ ] **Step 2: Run the focused test and confirm this new case fails because the text layer is accepted.**

Run from the repository root:

```bash
docker run --rm --add-host host.docker.internal:host-gateway -v "$PWD:/r" -v "/private/tmp/claude-501/-Users-khaled-saleh-kl1-MyStuff-All-Programming-Files-9XAIPal-VPS/0df7da94-8918-4b81-8cdc-75713469848e/scratchpad/codex/../empty-file.env:/r/backend/.env:ro" -w /r/backend --entrypoint sh $(docker inspect -f '{{.Config.Image}}' tracing-runner-20260926) -lc 'env -u PYTHONPATH STORAGE_ROOT=/tmp/test-storage DEBUG=true POSTGRES_DB=9xaipal_test POSTGRES_HOST=host.docker.internal POSTGRES_PORT=55439 REDIS_URL=redis://host.docker.internal:55440/0 /opt/arabic-test-venv/bin/python -m pytest -q -p no:cacheprovider --tb=short tests/test_arabic_ocr_fallback.py 2>&1 | tail -25'
```

Expected: the new case fails because its page markdown contains the PDF text instead of `UNREADABLE_PAGE_MARKER`.

- [ ] **Step 3: Count body words after excluding page-number and recognized running-furniture lines, and require at least eight body words.**

Keep the existing letter-count and Arabic/Latin quality checks. Add anchored matching for page-number lines (including `Page 23 of 120`) and recognizable furniture such as confidential-copy, annual-report, copyright, and rights-reserved lines. Count Unicode letter words of length at least two on remaining lines. Do not discard short lines generally: QIMMA's table uses short cell lines.

- [ ] **Step 4: Rerun the focused fallback test file in the same disposable container.**

Expected: all tests pass, including existing sparse-text rejection and English/Arabic text-layer recovery.

- [ ] **Step 5: Commit Task 1.**

```bash
git add backend/app/extraction/arabic_ocr.py backend/tests/test_arabic_ocr_fallback.py
git commit -m "fix(arabic): reject page furniture text layers"
```

### Task 2: Detect reversed Arabic by orientation plausibility

**Files:**
- Modify: `backend/app/extraction/arabic_ocr.py`
- Test: `backend/tests/test_arabic_ocr_fallback.py`

**Interfaces:**
- Consumes: `_has_reversed_arabic_words(text: str) -> bool` from the existing quality gate.
- Produces: the same private helper with broader orientation comparison; no new dependency or public interface.

- [ ] **Step 1: Add the review's exact arbitrary-vocabulary reversed input to the broken-Arabic marker test.**

```python
"باتك ديدج ريبك ليمج حضاو مادختسلال ثيدح ديفم تباث"
```

- [ ] **Step 2: Run the focused test and confirm the new case fails because the reversed layer is accepted.**

Use the same disposable-container pytest command from Task 1. Expected: only the new arbitrary-vocabulary case fails its marker assertion.

- [ ] **Step 3: Compare forward and character-reversed Arabic token orientation.**

Retain the current impossible-initial and common-word checks. Add a small, documented positional scoring model that rewards plausible Arabic word-initial and word-final letters and common clitic/article prefixes and inflectional suffixes. Score tokens both as extracted and character-reversed; reject only when the reverse score exceeds the forward score by a clear margin across multiple Arabic tokens. Do not add external dependencies or expand a fixed word dictionary as the sole detector.

- [ ] **Step 4: Run the entire focused fallback test file in the disposable container.**

Expected: the new review input and existing reversed/presentation-form/mojibake cases keep the marker; good Arabic and QIMMA-compatible English text still recover; the whole focused file passes.

- [ ] **Step 5: Inspect QIMMA page 23 locally through the same PyMuPDF extraction and quality helper.**

Record whether it is accepted, extracted character/Arabic/Latin-letter counts, and the first 200 characters in the report. Mount the PDF read-only into a throwaway container if running the check there; never contact an external service.

- [ ] **Step 6: Commit Task 2.**

```bash
git add backend/app/extraction/arabic_ocr.py backend/tests/test_arabic_ocr_fallback.py
git commit -m "fix(arabic): detect reversed text layer word order"
```

### Task 3: Report and final verification

**Files:**
- Modify: `/private/tmp/claude-501/-Users-khaled-saleh-kl1-MyStuff-All-Programming-Files-9XAIPal-VPS/0df7da94-8918-4b81-8cdc-75713469848e/scratchpad/codex/report-S.md`

**Interfaces:**
- Consumes: both focused-test results, commit hashes, and QIMMA page-23 check from Tasks 1 and 2.
- Produces: a `Round 2` report section appended to the existing report without rewriting Round 1.

- [ ] **Step 1: Append the Round 2 findings, quality-gate rules, changed files, focused test results, commit hashes, and QIMMA page-23 result.**
- [ ] **Step 2: Verify the worktree is clean and the branch is still `fix/unreadable-page-text-layer`; verify the external report file contains the appended Round 2 section.**

Expected: all implementation changes and this plan are committed, the requested external report is written, and no push or prohibited git/container operation has occurred.
