# Plan: pin MinerU 3.4.4 and retire the chunker repair heuristics

> **What this is:** the next actionable work after the parser evaluation closed. Four steps, in
> cost order, ending in a measured decision about deleting ~280 lines of defect-repair code and
> one whole subsystem.
>
> **How to read it:** §1 where things stand → §2 what already exists on disk → §3 step 1 (do this
> first, 5 min) → §4 step 2 (the measurement) → §5 the delete gate → §6 what must NOT be deleted.
>
> **Companions:** [pdf-parser-evaluation.md §0](pdf-parser-evaluation.md): the S0 results that
> produced this plan · [ingestion-pipeline.md](../02-architecture/ingestion-pipeline.md).
>
> **Status:** step 1 done · steps 2-3 measured (see §0) · **Blocked on:** 3-5 scanned PDFs
> before `reconstruct_reading_order` can be removed · **Last run:** 2026-07-26 (n=28)
> **Picking this up cold?** Read §1 and §2, then run §3. Everything needed is already installed.

---

## 0. RESULTS: measured 2026-07-26 (n=28)

> **Headline: keep almost everything.** Of the ~280 lines under suspicion, **only the
> `reconstruct_reading_order` subsystem passed its delete gate.** Both equation heuristics
> survived, for reasons the original n=2 evidence could not have surfaced.

### 0a. Corpus

28 papers, **16 two-column** (gate: ≥15 ✓). Fetched by
[`scripts/fetch_eval_corpus.sh`](../../scripts/fetch_eval_corpus.sh); all parsed with
`-m auto -b pipeline -l en`, MinerU 3.4.4, host, 0 failures. Total parse time ~28 min.

⚠ **Deviation from the §4 spec, stated plainly:** it asked for 4 published journal PDFs and 3
genuinely scanned/image-only papers. arXiv serves preprints, and publisher PDFs need
subscriptions, so those classes were substituted with 5 papers from 1993–2000 (degraded
typography) and RevTeX/emulateapj physics preprints (reliably two-column). **Scanned/OCR-path
documents remain untested**: see §0f.

⚠ Layout is measured, not assumed. Several "expected two-column" CS papers came back
single-column: arXiv preprints frequently differ from the published camera-ready (GoogLeNet is
the clearest case). The first batch yielded only 9/20 two-column, which is why a second batch was
fetched rather than the gate being lowered.

### 0b. Verdict table

| Heuristic | Metric | Result | **Decision** |
| --- | --- | --- | --- |
| `reconstruct_reading_order` (task + 3 columns + endpoint + UI button) | inversions | **0 / 28**, incl. 16 two-column | **DELETE candidate: gate passed** |
| `_stitch_split_equations` + label helpers (~200 lines) | orphan labels | **8** across 2 papers | **KEEP: gate failed** |
| `_normalize_math_glyphs` (~40 lines) | glyphs inside math | 0 on MinerU · **1,930 on PyMuPDF fallback** | **KEEP: load-bearing elsewhere** |
| `_normalize_inline_math` (~42 lines) | glyphs inside math | 0 on MinerU, but delegates to the above | **KEEP: entangled** |

### 0c. Reading order: clean, 0/28

Zero inversions on every paper, including 16 two-column ones spanning CVPR, ICCV, RevTeX (PRL/PRD)
and emulateapj (ApJ/A&A) formats. Paragraph-level flow was verified directly on two-column pages:
left column emitted in full, then right column, with the break landing mid-sentence and resuming
correctly.

**→ MinerU 3.4.4 does not exhibit the reading-order defect `reconstruct_reading_order` was built to
repair.** The subsystem appears vestigial, written against an older MinerU, most likely one
predating the 3.x line.

### 0d. Equation labels: the heuristic survives

`orph` was 0 across the first 20 papers. Expanding to 28 found **8 real orphan labels**:
Planck-2015 (6) and WMAP5 (2). These are genuine defects, not artifacts:

```text
[230] equation  $$ \tau = 0.066^{+0.016}_{-0.016}, z_re = 8.8 ... $$
[231] text      (17c)     <== label stranded from its equation
[232] text      +lensing;

[606] text      where
[607] text      (66)      <== label emitted BEFORE the equation it belongs to
[608] equation  $$ g_1 = \frac{0.0783(\Omega_b h^2)^{-0.238}}{1 + ...} $$
```

Both are dense cosmology papers using sub-lettered equations (`17c`, `23b`, `29b`, `40b`, `77c`).
The defect is narrow (2 of 28 papers, ~7%) but real, and silent when it happens.

⚠ **This is exactly why the gate required n=20 rather than the n=2 that produced the original
"probably dead code" reading.** Two easy papers said delete; twenty-eight said keep.

**→ `_stitch_split_equations` and the label helpers stay.**

### 0e. Glyph normalizer: kept for a reason the metric nearly missed

Two corrections were needed before this could be answered:

1. **The original M3 metric measured the wrong population.** It counted Unicode glyphs in
   *non-equation prose*. But `_normalize_math_glyphs` is only ever applied to text **inside** math
   delimiters ([`chunker.py`](../../backend/app/extraction/chunker.py) L331, L421-436, L1064),
   and L428 says so explicitly. The prose hits (`ΛCDM` ×196 in Planck, `3.9σ`, `3×3 conv`) are
   terminology, not broken math. Recounted against equation bodies and inline `$…$` spans:
   **0 across all 28 papers.**
2. **But the MinerU path is not the only caller.** L1061/L1064 sit inside
   `create_chunks_from_markdown`, the **PyMuPDF fallback** path, which the 28-paper run never
   exercised. Measured directly:

   | Paper | Glyphs inside math (PyMuPDF fallback) |
   | --- | --- |
   | perelman-entropy | **844** |
   | planck-2015 | 519 |
   | wmap5 | 481 |
   | resnet | 86 |
   | **Total** | **1,930** |

   e.g. `$$ (gij)t = −2(Rij + ∇i∇jf ), $$`: raw `∇` inside display math, precisely what the
   function converts to `\nabla`.

**→ `_normalize_math_glyphs` is dead on the MinerU path and load-bearing on the fallback path.**
Deleting it on the MinerU evidence alone would have silently broken math rendering for every
document extracted with `ALLOW_PYMUPDF_FALLBACK=true`.

### 0f. What remains untested

- **Scanned / image-only PDFs.** The OCR code path was never exercised: no scanned document was
  obtainable from arXiv. This is the most likely remaining home for the equation defects, and it
  is the one gap that could still change the reading-order verdict.
- **Published journal PDFs** (Elsevier/Springer/Wiley typesetting), for the same access reason.
- `hybrid-engine` was not re-run at n=28; the n=2 result (slower, worse heading classification)
  stands unchallenged but is thin.

### 0g. Recommended next actions

| # | Action | Confidence |
| --- | --- | --- |
| 1 | ✅ **Done**: `mineru[core]>=3.4.4` pinned in `requirements.txt` | Certain |
| 2 | **Keep both equation heuristics.** Record here that they are guarding the fallback path and sub-lettered equations, not mainstream MinerU output | High: measured |
| 3 | **`reconstruct_reading_order`: strong delete candidate**, but get 3–5 scanned PDFs through it first (§0f). It is the only untested class that could flip the result | Medium: pending §0f |
| 4 | Add a regression test asserting `orph == 0` on a fixture, so a future MinerU upgrade cannot silently reintroduce the defect | N/A |

---

## 1. Where things stand

The parser evaluation asked whether to replace MinerU. **Answer: no.** MinerU 3.4.4 on the
`pipeline` backend this app already uses produced correct two-column reading order (zero
inversions) and zero fragmented equations. Full results:
[pdf-parser-evaluation.md §0](pdf-parser-evaluation.md).

That closed one question and opened a better one: **if MinerU no longer has the defects, is the
code that repairs them still needed?**

Four things are suspected dead, totalling ~280 lines plus a subsystem:

| Suspect | ~Lines | Evidence so far (n=2 papers, 4 runs) |
| --- | --- | --- |
| `_stitch_split_equations` + fragment helpers | ~200 | 0 orphan `(N)` labels, 0 mid-construct splits |
| `_normalize_math_glyphs`, `_normalize_inline_math` | ~82 | ~0 glyph leakage; the 2 hits were bullets in prose |
| `reconstruct_reading_order` (task + 3 columns + endpoint + UI button) | subsystem | 0 reading-order inversions |

⚠ **n=2 is not enough to delete a safety net.** Both test papers were clean, born-digital arXiv
PDFs with well-formed LaTeX. "Zero hits on two easy papers" is equally consistent with *the defect
is fixed* and with *the triggering cases were never tested*. This plan closes that gap before
anything is removed.

**The failure mode if you skip the measurement:** deleting the equation repair when the defect
still occurs in, say, 1-in-10 papers produces **silently corrupted math**: no error, no failed
job, just wrong rendering in the reader, surfacing weeks later with the cause 280 deleted lines
back.

---

## 2. What already exists on disk

Set up 2026-07-25. Nothing here needs redoing.

| Thing | Where | Note |
| --- | --- | --- |
| Python venv | `backend/.venv` | Python 3.11.0 |
| MinerU **3.4.4** | `backend/.venv/bin/mineru` | Host install, no Docker, no CUDA. Models cached in `~/.cache` |
| Measurement harness | [`scripts/analyze_content_list.py`](../../scripts/analyze_content_list.py) | Scores layout + M1/M2/M3, `--summary` for batch runs |
| Single-column fixture | `samples/attention-is-all-you-need.pdf` | ⚠ NeurIPS format, **single-column**, despite what earlier notes claimed |
| Two-column fixture | `samples/resnet-1512.03385.pdf` | CVPR format: the real adversarial case |

Smoke-test the harness at any time:

```bash
cd /Users/ezz/Documents/GitHub/ScholarFlow
backend/.venv/bin/python scripts/analyze_content_list.py \
  <any>/*_content_list.json
```

⚠ **The harness checks layout first, and says so.** A reading-order pass on a single-column paper
proves nothing: that mistake was made once already and produced a false green.

---

## 3. Step 1: pin MinerU (do this first, ~5 minutes, zero risk)

**This is the real finding of the whole evaluation.** `mineru` is not a dependency at all today:
not in `requirements.txt`, not installed by any setup step, purely manual. That is why
[setup.md](../01-orientation/setup.md) has to warn about it separately.

```diff
  # backend/requirements.txt
+ # PDF extraction. 3.4.4 verified on Apple Silicon (host, no CUDA) 2026-07-25.
+ # 3.x removed the layoutreader model for licensing; 3.4.4 nonetheless scores
+ # 0 reading-order inversions on two-column papers: see docs/plans/pdf-parser-evaluation.md
+ mineru>=3.4.4
```

Done when: a fresh `pip install -r requirements.txt` yields a working `mineru`, and the
"MinerU is not in requirements.txt" warning can come out of `setup.md` §1.

⚠ Do **not** switch the app to `-b hybrid-engine`. Measured 8.5× slower, more equation
fragmentation, and it misclassified an author byline as headings. Also its default
`--effort medium` **silently disables image/chart analysis**, which this app's figure pipeline
depends on. Keep `-b pipeline`.

---

## 4. Step 2: the n=20 measurement (~1 hour)

### Corpus

Twenty PDFs, **at least 15 two-column**, deliberately including the hard cases the two existing
fixtures do not cover:

| Class | Count | Why it matters |
| --- | --- | --- |
| Two-column arXiv preprints (CVPR/IEEE style) | 8 | The baseline adversarial case |
| Published **journal** PDFs | 4 | Different LaTeX macro packages than preprints |
| **Scanned / image-only** papers | 3 | Forces MinerU into OCR mode: a different code path entirely |
| Macro-heavy / custom-package papers | 3 | Where equation fragmentation is most likely |
| Single-column (control) | 2 | Already have one |

Pull them from the actual reading list, not a benchmark set: the question is whether the
heuristics fire on **your** documents.

### Run

```bash
cd /Users/ezz/Documents/GitHub/ScholarFlow
OUT=/tmp/mineru-n20; mkdir -p "$OUT"

for pdf in samples/eval/*.pdf; do
  backend/.venv/bin/mineru -p "$pdf" -o "$OUT" -m auto -b pipeline -l en
done

backend/.venv/bin/python scripts/analyze_content_list.py --summary \
  "$OUT"/*/auto/*_content_list.json
```

⚠ **This is zsh.** Unquoted `$var` does *not* word-split, so do not collapse the mineru flags into
a variable: that failure cost a run today. Write the flags literally.

### Read

The `--summary` table prints one row per paper with the columns that matter:

```text
  file                          layout          inv  orph  adj  tail  glyph
  ...                           two-column        0     0    0     0      0
  two-column papers: 17/20   ✓
  totals: inversions=0 orphans=0 adjacent=0 open_tail=0
```

---

## 5. The delete gate

Remove a heuristic **only** when its column is `0` across **all 20 rows** and **≥15 rows are
two-column**. Anything non-zero means the code still earns its keep: record the count here and
stop.

| Column | Guards | Delete if 0 across all rows |
| --- | --- | --- |
| `inv` | `reconstruct_reading_order` subsystem | Task, `documents.reading_order` + `reading_order_model` + `reading_order_updated_at`, the endpoint, the UI button |
| `orph` + `adj` + `tail` | `_stitch_split_equations` + `_looks_like_equation_label` + `_is_short_math_fragment` + `_trailing_open_fragment` + `_leading_close_fragments` (~200 lines) | All of them |
| `glyph` | `_normalize_math_glyphs`, `_normalize_inline_math` (~82 lines) | Both, ⚠ but read the note below first |

⚠ **The `glyph` column needs judgement, not just a zero check.** On the 2026-07-25 run ResNet
scored `glyph=2`, but inspecting them showed both were `×` in ordinary prose: *"more training
iterations (3×)"* and a similar case. That is a literal multiplication sign that renders correctly
as-is; it is **not** broken inline math that failed to reach the formula model.

So a non-zero `glyph` count means *inspect*, not *keep*. Run the harness without `--summary` to see
the offending text, then classify:

| What you see | Meaning |
| --- | --- |
| `×`, `≤` etc. inside ordinary prose (`3×`, `n ≤ 5`) | **Harmless.** Renders fine. Does not justify keeping the normalizer |
| A recognisable formula rendered in glyphs instead of LaTeX (`∑ᵢ xᵢ√n`) | **Real defect.** Keep the normalizer and record the count |

Only the second class is the failure `_normalize_math_glyphs` was written for.

**Order of removal, cheapest to reverse first:**

1. `_normalize_math_glyphs` / `_normalize_inline_math`: pure functions, trivially revertible
2. `_stitch_split_equations` + helpers: pure functions, still contained
3. `reconstruct_reading_order`, **last**. It spans a Celery task, three DB columns, an endpoint,
   and a UI button; dropping the columns is the only irreversible part, so drop code first and
   columns in a later change

After each removal, re-chunk an already-ingested paper and diff the chunks:

```bash
curl -X POST http://localhost:8000/api/v1/papers/<id>/rechunk
```

`rechunk` re-runs the chunker against cached extraction without re-running MinerU: it is the
cheap harness for exactly this.

---

## 6. What must NOT be deleted

⚠ The "~400 lines of MinerU-specific code" framing used earlier was too broad. One block in that
count is **not** a defect repair:

| Code | ~Lines | Why it stays |
| --- | --- | --- |
| `_parse_table_body_to_json` + `_SimpleTableParser` | ~93 | Parses MinerU's HTML `table_body` into structured JSON. MinerU **still emits HTML tables**, and so does every alternative parser. This is a required transformation, not a workaround: deleting it breaks `chunk_type='table'` and `table_json` outright |

Also out of scope: the chunker's heading/paragraph/footnote splitting, `_renumber_sequences`, and
anything touching `sequence_id`. This plan removes repairs for defects that no longer occur. It
does not change chunking semantics.

---

## 7. Done when

- [ ] `mineru>=3.4.4` pinned in `requirements.txt`; the manual-install warning removed from `setup.md`
- [ ] 20-paper summary table pasted into this doc, with ≥15 two-column
- [ ] Each heuristic either deleted (column was 0) or explicitly kept with its hit count recorded here
- [ ] `rechunk` diff clean on at least 3 papers after removals
- [ ] [roadmap.md](../roadmap.md) updated: the two `[planned]` MinerU entries resolved
- [ ] This plan archived to `docs/archive/YYYY-MM-DD/`
