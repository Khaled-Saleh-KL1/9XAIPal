# Plan — PDF parser evaluation: add a second extractor, route by document length

> ## ⚠ SUPERSEDED BY S0 RESULTS (2026-07-25) — do not act on §1 as written
>
> **S0 ran. The premise did not survive it.** MinerU **3.4.4**, on the `pipeline` backend this
> app already uses, produced **correct two-column reading order with zero inversions** on a
> genuine two-column paper, and **zero fragmented equations / zero orphan `(N)` labels** on both
> test papers. The reading-order failure that motivates this entire plan **did not reproduce**.
>
> **Current recommendation: do not adopt a second parser. Upgrade MinerU and re-test
> `reconstruct_reading_order` for removal.** Full results and revised verdict: [§0](#0-s0-results).
>
> §1–§9 below are retained as the evaluation framework and remain accurate about *other* parsers;
> the §1 verdict is **[historical]** — it was written before measurement.
>
> **What this is:** the decision framework for whether to replace MinerU, and the measurement
> protocol that settles it. ⚠ Its original conclusion (adopt PaddleOCR-VL for papers) is
> superseded — see the banner above.
>
> **How to read it:** §1 the verdict → §2 why leaderboards can't answer this → §3 root cause of
> the reading-order pain → §4 the measurement protocol (the decisive part) → §5 extractor
> interface → §6 what deletes and what doesn't → §7 the Docker/MLX conflict → §8 failure catalog
> → §9 sharp edges → §10 segmentation.
>
> **Companions (detail):**
> [ingestion-pipeline.md](../02-architecture/ingestion-pipeline.md) — the pipeline being extended ·
> [runtime-topology.md](../01-orientation/runtime-topology.md) — where the worker runs ·
> [roadmap.md](../roadmap.md) — the `reconstruct_reading_order` subsystem at stake.
>
> **Status:** closed — decision made, do not act on §1 · **Reflects code as of:** 2026-07-26
> **Target hardware (verified):** Apple M4 Max · 48 GB unified · macOS 15.7.4
>
> **Evidence tags used throughout:** `[official]` maintained leaderboard or open scorer ·
> `[vendor]` self-reported · `[vendor-cross]` one vendor's number for a competitor, run on the
> claimant's own harness — the weakest class.

---

## 0. S0 results

> **Status:** measured · **Run:** 2026-07-25 · **Hardware:** Apple M4 Max · 48 GB · macOS 15.7.4
> **MinerU:** 3.4.4 (current release), installed to `backend/.venv`, run on the **host** — no
> Docker, no CUDA.
> **Reproduce:** `mineru -p <pdf> -o <out> -m auto -b pipeline -l en`

### 0a. What was measured

Two backends × two papers. Arm 1 is exactly what
[`mineru_client.py:258-264`](../../backend/app/extraction/mineru_client.py) runs today.

| Arm | Invocation |
| --- | --- |
| **pipeline** | `-m auto -b pipeline -l en` — the app's current, hardcoded configuration |
| **hybrid** | `-b hybrid-engine --effort high` — MinerU 3.4's default backend + image analysis |

| Paper | Pages | Layout (verified by bbox clustering) |
| --- | --- | --- |
| `attention-is-all-you-need.pdf` | 15 | ⚠ **Single-column** (NeurIPS format) — 89/103 text blocks share one left edge |
| `resnet-1512.03385.pdf` | 6 | **Two-column** (CVPR format) — clusters at x≈80 (56 blocks) and x≈480 (60 blocks) |

⚠ **Correction to §4 below:** it names `attention-is-all-you-need.pdf` as the "two-column, known
adversarial case". That is wrong — it is equation-dense but single-column. A two-column paper had
to be fetched to test the actual claim. Any future run must verify layout by bbox clustering
before trusting a reading-order result.

### 0b. M1 — reading order

| Arm | Paper | Numbered headings | **Inversions** | Sequence |
| --- | --- | --- | --- | --- |
| pipeline | Attention (1-col) | 22 | **0** | `1 2 3 3.1 3.2 3.2.1 3.2.2 3.2.3 3.3 3.4 3.5 4 5 5.1 5.2 5.3 5.4 6 6.1 6.2 6.3 7` |
| hybrid | Attention (1-col) | 22 | **0** | identical |
| **pipeline** | **ResNet (2-col)** | 11 | **0** | `1 2 3 3.1 3.2 3.3 3.4 4 4.1 4.2 4.3` |
| hybrid | ResNet (2-col) | 11 | **0** | identical |

Heading monotonicity alone is necessary but not sufficient, so paragraph flow was checked directly
on a two-column page. It is correct: all left-column blocks emitted in order, then all
right-column blocks, with the column break landing mid-sentence and continuing correctly —
block 43 ends `"The function $\mathcal{F}…"`, block 44 resumes `"ReLU [29] and the biases are
omitted…"`.

**→ MinerU 3.4.4 gets two-column reading order right, on the backend the app already uses.**

### 0c. M2 — equation integrity

| Arm | Paper | Equation blocks | Adjacent pairs | Open-tail splits | **Orphan `(N)` labels** |
| --- | --- | --- | --- | --- | --- |
| pipeline | Attention | 5 | 0 | 0 | **0** |
| hybrid | Attention | 7 | 2 | 0 | **0** |
| pipeline | ResNet | 2 | 0 | 0 | **0** |
| hybrid | ResNet | 2 | 0 | 0 | **0** |

**→ Zero orphan equation labels and zero mid-construct splits in every run.** That is precisely
the defect `_stitch_split_equations` (~120 lines) exists to repair.

### 0d. M3 — Unicode-vs-LaTeX leakage

Total literal math glyphs leaking into non-equation text blocks: **2** (Attention/pipeline),
**1** (Attention/hybrid), and on inspection both were bullet characters in ordinary prose, not
leaked inline math.

**→ The Unicode-glyph problem does not reproduce on 3.4.4.** There is no inline-formula toggle to
flip, because there is nothing to fix on the MinerU path.

⚠ **[superseded 2026-07-26]** This section then concluded `_normalize_math_glyphs` +
`_normalize_inline_math` were dead weight. **That conclusion was wrong**, for two reasons found
later: this metric counted glyphs in *prose* rather than inside math delimiters (the only place
the function is applied), and the **PyMuPDF fallback path** — never exercised here — leaks
**1,930** glyphs inside math. Both functions are kept. See
[mineru-heuristic-removal.md §0e](mineru-heuristic-removal.md).

### 0e. Wall-clock (host, Apple Silicon)

| Arm | Attention (15 pp) | ResNet (6 pp) |
| --- | --- | --- |
| **pipeline** | **77 s** ¹ | **30 s** |
| hybrid `--effort high` | 388 s ¹ | 256 s |

¹ First run — includes one-time model download. ResNet timings are warm and are the honest ones.

**→ `pipeline` is 8.5× faster than `hybrid-engine` on warm runs and scored identically or better
on every quality metric.** The newer backend was slower, fragmented equations more (2 adjacent
pairs vs 0), and misclassified the author byline as headings (32 headings vs 22, promoting
"Ashish Vaswani", "Noam Shazeer" etc. to `text_level`) — which would corrupt `heading_path`
breadcrumbs in this app.

### 0f. Revised verdict

**Do not adopt a second parser. The premise of §1 did not survive measurement.**

| Original §1 claim | S0 finding |
| --- | --- |
| MinerU gets two-column reading order wrong | ❌ **Did not reproduce.** Zero inversions, correct paragraph flow |
| A learned ordering head would fix it | Moot — there is nothing to fix on 3.4.4 |
| `hybrid-engine` is the better modern backend | ❌ 8.5× slower, more equation fragmentation, worse heading classification |
| The inline-formula toggle may delete ~82 lines | ✅ **Likely, but for a different reason** — the defect is absent, not toggle-able |
| Docker on macOS blocks acceleration | ✅ Still true, and now moot for MinerU — `pipeline` on the host is fast enough |

### 0g. What to do instead

| # | Action | Rationale |
| --- | --- | --- |
| 1 | **Pin `mineru>=3.4.4` in `requirements.txt`** | It is not a dependency at all today — it is a manual install, unpinned. This is the actual fix |
| 2 | **Run the §4 protocol at n=20 before deleting anything** | n=2 is strong evidence, not proof. `reconstruct_reading_order` should not be deleted on two papers |
| 3 | **Do not switch to `hybrid-engine`** | Measured worse on every axis here. ⚠ And its default `--effort medium` silently disables image/chart analysis, which this app's figure pipeline depends on |
| 4 | **Treat `_stitch_split_equations` / glyph normalizers as candidates for removal** | Zero hits across 4 runs. Gate removal on the n=20 result |
| 5 | **Keep this document** | The §2 benchmark-provenance analysis and the §5 `Block` interface stay valuable if a parser swap is ever revisited |

⚠ **What S0 did not establish.** n=2 papers, one of each layout. It does not tell us which MinerU
version the original reading-order pain was observed on — plausibly 2.x, before this repo's
`reconstruct_reading_order` was written. That subsystem may be **vestigial rather than wrong**, and
confirming it needs the n=20 run.

---

## 1. The verdict [historical — superseded by §0]

> ⚠ Written before measurement. Retained for the reasoning; **the conclusion is wrong.** See §0f.

**Add, don't replace. Route by document length.**

| Document | Extractor | Why |
| --- | --- | --- |
| Papers (`doc_kind='paper'`, < ~100 pp) | **PaddleOCR-VL 1.6** `[candidate]` | Learned reading-order head; typed block list; first-party Apple Silicon path verified on M4 |
| Books (`doc_kind='book'`, 100 pp+) | **MinerU 3.4.x** (keep) | Sliding-window + streaming-to-disk for ultra-long documents. Nothing else in the field advertises anything comparable, and a 656-page book already OOM-kills this worker |

Three hard gates decided the candidate, and PaddleOCR-VL is the only option clearing all three:

1. **Apple Silicon, first-party.** Verified on M4 — and this machine is an M4 Max, so the
   "other Apple Silicon unconfirmed" caveat does not apply here.
2. **A real CLI** (`paddleocr doc_parser`) — [`mineru_client.py`](../../backend/app/extraction/mineru_client.py)
   already shells out to a binary. Same integration shape, no architecture change.
3. **Typed, ordered, page-indexed blocks.** `parsing_res_list` carries `block_label`,
   `block_bbox`, `block_order`, `block_id` — list order *is* reading order. That maps onto the
   existing heading/text/math/table/figure/footnote taxonomy nearly 1:1.

Apache 2.0, no license asterisks.

**Fallback: GLM-OCR** — MIT model, `glmocr parse` CLI, same PP-DocLayout ordering lineage, and the
best formula numbers reported anywhere (96.5 UniMERNet `[vendor]`), which matters given heavy
LaTeX. Demoted for two reasons: the MLX path requires **two Python environments talking over HTTP**
(mlx-vlm needs `transformers>=5.0.0rc3`, which conflicts with the GLM-OCR SDK's pin for
PP-DocLayout-V3), and its 29.6 on ParseBench against PaddleOCR-VL-1.5's 66.0 `[vendor-cross]` is an
outlier that smells like a harness format mismatch but is unexplained.

**Third, on schema alone: dots.ocr** — an eleven-category label set including `Formula` and
`Footnote` as first-class types, LaTeX formulas, HTML tables, native mlx-vlm support. Closest
taxonomy match that exists. No CLI, so it needs a wrapper.

**Ruled out:** Nanonets OCR-3 (no open weights, 35B, and the leaderboard it tops is operated by
Nanonets), Chandra 2 (CUDA-first, commercial self-hosting needs a license, ~66 s/page on a 4090),
LightOnOCR-2 (no typed blocks, no CLI, forced fp32 on MPS), Docling as a parser (steal
`DoclingDocument` as a schema idea, not the extractor).

---

## 2. Why the leaderboards were never going to answer this

The single most useful finding, and it invalidates my earlier "0.64 points is noise" framing —
which was wrong not because the gap is bigger, but because **that composite does not measure
reading order at all**:

```text
OmniDocBench "Overall" = ( (1 - text_edit_distance)*100 + table_TEDS + formula_CDM ) / 3
                           └── text ──┘                    └─ tables ─┘   └ formulas ┘

                         reading order:  NOT IN THE COMPOSITE
```

Every headline OmniDocBench number — PaddleOCR-VL-1.6's 96.33, MinerU2.5-Pro's 95.69, both
`[vendor]` — is **silent on the metric that drives this entire decision.** Comparing them to
decide a reading-order question is a category error.

Four distinct mechanisms inflate the numbers in this space, all worth recognising on sight:

| Mechanism | Example |
| --- | --- |
| Competitor scores run on the claimant's harness | Infinity-Parser2 reports MinerU2.5 at 75.2 and PaddleOCR-VL-1.5 at 78.5, asterisked as evaluated by internal tools `[vendor-cross]` |
| Category exclusion | LightOnOCR's 83.2 and GLM-OCR both drop headers/footers — a category where empty output scores perfectly |
| Redefined metrics | Nanonets reclassifies 437 of 864 failures as "evaluator brittleness" and reports 94.9% against a leaderboard 87.4% |
| Self-refereed leaderboards | The IDP Leaderboard is operated by Nanonets, whose model tops it. OmniDocBench is maintained by OpenDataLab — MinerU's own team. Both cut both ways |

⚠ Also note MinerU's incumbency advantage is not neutral: OmniDocBench is maintained by MinerU's
own organisation. That is a reason to discount MinerU's OmniDocBench standing, not to trust it.

**What OmniDocBench *does* offer is the answer, just not in the composite:** reading order is
scored separately as Normalized Edit Distance over text components, display formulas get their own
CDM metric, and pages are stratified by layout — including **126 double-column pages**. Filter to
double-column + academic paper, read only the reading-order NED and formula CDM columns, and the
benchmark answers the real question directly.

---

## 3. Root cause: why `reconstruct_reading_order` exists

This is the finding that reframes the whole subsystem.

MinerU **used to be best in class** at multi-column reading order — on OmniDocBench v1.0, MinerU
and Mathpix retained the best reading-order performance on multi-column layouts. Then:

```text
MinerU 3.0 relicensing
  │
  ├─ removed doclayoutyolo   (AGPLv3)
  ├─ removed mfd_yolov8      (AGPLv3)
  └─ removed layoutreader    (CC-BY-NC-SA 4.0)
                └── ⚠ THE DEDICATED READING-ORDER MODEL
                    0.9.0 had refactored the sorting module
                    specifically to use it, "ensuring high
                    accuracy in various layouts"
  │
  ▼
replaced by spatial inference over readable content
  │
  ▼
README limitation (current): "Reading order is determined by the model based on
the spatial distribution of readable content, and may be out of order in some
areas under extremely complex layouts."
  │
  ▼
this repo: reconstruct_reading_order Celery task + documents.reading_order JSONB
           + reading_order_model + reading_order_updated_at + an endpoint + a UI button
```

**The workaround in this codebase is downstream of a licensing decision, not a technical ceiling.**
layoutreader was dropped for license cleanliness, the consequence was documented as an inherent
limitation, and the 3.x roadmap went to licensing, long-document memory, throughput, and OCR/table
accuracy — not to recovering reading order. There is a matching public issue (#3591, wrong reading
order on a two-column PDF, pipeline backend) with no landed fix.

PaddleOCR-VL attacks exactly this gap architecturally rather than geometrically: **PP-DocLayoutV2
is two sequentially connected networks** — an RT-DETR detector whose boxes and class labels feed a
**pointer network responsible for ordering them**, with absolute 2D positional encodings, class
label embeddings, and a geometric bias mechanism modelling pairwise relationships. Its paper names
the contrast explicitly against "MinerU2.5, Dolphin".

A trained ordering head versus spatial inference is a real architectural difference. It is the
single best reason to believe the LLM reordering pass could be deleted — **and it is a hypothesis
this plan tests, not a result it assumes.**

---

## 4. The measurement protocol

⚠ **Run this before writing any adapter code.** Every number in §1–3 is someone else's corpus.
Two numbers decide this, and neither appears on any leaderboard.

### Corpus

Twenty real two-column arXiv PDFs from the actual reading list — not a benchmark set. Include
[`samples/attention-is-all-you-need.pdf`](../../samples/) (two-column, equation-dense, the known
adversarial case), plus at least three with heavy LaTeX macros and two published journal PDFs,
which behave differently from preprints.

### Arms

| Arm | Invocation | Notes |
| --- | --- | --- |
| A | MinerU 3.4.x, `hybrid-engine` | ⚠ Not the current setup — see §7. `vlm-engine` auto-detects Apple Silicon and routes to MLX |
| B | PaddleOCR-VL 1.6, `paddleocr doc_parser` | Primary candidate |
| C | GLM-OCR, `glmocr parse` | Only if A/B are close; the two-env setup costs a day |

### The two metrics

```text
M1  READING ORDER          per page:  is column order correct?  (binary, by hand)
    ─────────────────      report:    fraction of pages correct, per arm
    decides:               can reconstruct_reading_order be deleted?
    threshold:             ≥ 95% unaided, or the subsystem stays

M2  EQUATION INTEGRITY     per document:  (a) display equations split across >1 block
    ─────────────────                     (b) orphan label blocks, e.g. a lone "(1)"
    report:                counts per arm, normalised per 10 pages
    decides:               does _stitch_split_equations (~120 lines) survive?
    threshold:             near-zero on both counts, or the repair code stays
```

Record wall-clock per document per arm as a third column. **No public Apple Silicon throughput
numbers exist** for any of these — every published figure is H100/A100 (LightOnOCR-2 at 5.71 pp/s
on an H100; MinerU2.5-Pro at 2.12 fps on an A100; GLM-OCR 1.86 pp/s). The only Apple datapoint
anywhere is relative and baseline-free: MinerU's `vlm-mlx-engine` claims a 100–200 % improvement
over `vlm-transformers`. You will be generating the first numbers that apply to this machine.

### Decision rule

| M1 (PaddleOCR-VL) | M2 (PaddleOCR-VL) | Action |
| --- | --- | --- |
| ≥ 95% | near-zero | Adopt for papers. Delete `reconstruct_reading_order`. Retire `_stitch_split_equations` behind the MinerU adapter |
| ≥ 95% | comparable to MinerU | Adopt for papers. Delete `reconstruct_reading_order`. **Keep the equation repair code** — port it to run per-adapter |
| < 95% | any | ⚠ **Do not switch.** The entire case rests on M1. Check whether MinerU 3.x's inline-formula toggle fixes M2 instead (§9) |

---

## 5. Extractor interface

Do not adopt any vendor's schema. Define one internal block record; write thin adapters.

The seam already exists — [`extract_pdf_sync`](../../backend/app/extraction/mineru_client.py)
returns `(output_dir, extractor_name)` and `documents.extractor` already records which ran
(`mineru` | `pymupdf_fallback`). This plan formalises it.

```python
# [spec] app/extraction/base.py
@dataclass
class Block:
    kind: Literal["heading","text","math","table","figure","footnote"]
    content: str                 # markdown; LaTeX for math; HTML for table
    page: int | None
    order: int                   # 0-based reading order AS THE PARSER REPORTS IT
    bbox: tuple[float,float,float,float] | None
    label: str | None            # e.g. equation tag "(1)"

def extract(pdf: Path, document_id: str) -> tuple[list[Block], str]:
    """-> (blocks in reading order, extractor_name)"""
```

| Adapter | Source shape | Est. effort |
| --- | --- | --- |
| MinerU | `<stem>_content_list.json` — typed blocks + page indices | Exists; wrap it |
| PaddleOCR-VL | `parsing_res_list[]` → `block_label`/`block_bbox`/`block_order`/`block_content` | **~50 lines** — near 1:1 |
| GLM-OCR | `result.json` + `result.md` + typed crops (`0_text.png`, `1_table.png`) | ~80 lines |
| dots.ocr | bbox + 11-category label + text | ~60 lines, no CLI |

**Tables transfer unchanged.** MinerU, PaddleOCR-VL, dots.ocr, GLM-OCR and Nanonets all emit
**HTML** tables, so `_parse_table_body_to_json` and `_SimpleTableParser` (~93 lines) survive any
switch untouched. That is ~93 of the 400 lines already accounted for as portable.

⚠ **Nobody emits MinerU-compatible `content_list.json`, and MinerU has changed its own shape
before** — 2.5 altered both `middle.json` and `content_list.json` when the VLM model added layout
types. Coupling the chunker to any vendor schema is the mistake this interface exists to prevent.
`DoclingDocument` is the nearest thing to a convergence point (MIT, reading order + per-item
provenance, designed as an adapter target) and is worth reading as prior art — but building the
internal record is cheaper than adopting Docling's model wholesale.

---

## 6. What deletes, and what doesn't

I over-promised on this earlier. The honest split:

| Code | ~Lines | Fate | Why |
| --- | --- | --- | --- |
| `reconstruct_reading_order` task + 3 `documents` columns + endpoint + UI button | subsystem | **Deletes if M1 passes** | Its entire reason for existing is §3 |
| `_parse_table_body_to_json` + `_SimpleTableParser` | ~93 | **Survives, portable** | Every candidate emits HTML tables |
| `_stitch_split_equations` + fragment helpers | ~200 | ⚠ **Probably survives** | See below |
| `_normalize_math_glyphs`, `_normalize_inline_math` | ~82 | **Likely deletes** for the new adapter | Candidates guarantee LaTeX by contract |

⚠ **Equation fragmentation is architecturally determined, and no benchmark measures it.**
MinerU masks inline formulas by coordinate, OCRs, then reinserts — so an equation and its `(1)`
label are two separately-detected regions with nothing obliging a merge. **PaddleOCR-VL, GLM-OCR
and dots.ocr all use the same layout-detect-then-recognize-per-region pattern, so the same failure
mode is structurally possible.** CDM scores per-formula symbol accuracy *given a formula*; it says
nothing about segmentation. Anyone claiming a specific parser fixes this is guessing.

The real trade-off, stated cleanly: **whole-page autoregressive models (LightOnOCR-2, Chandra,
Nanonets) cannot fragment blocks because they have no block concept** — but they also cannot give
a typed block list, which this app requires. This is a choice between two failure modes, not an
escape from both. Budget for `_stitch_split_equations` surviving; treat its deletion as upside.

The Unicode-glyph problem is more tractable: dots.ocr puts LaTeX in the *output specification*
("Formula: Format its text as LaTeX"), Nanonets converts equations to LaTeX and distinguishes
inline from display, and GLM-OCR's 96.5 UniMERNet `[vendor]` is a LaTeX-output metric it could not
score on without emitting LaTeX.

---

## 7. ⚠ The Docker / MLX conflict

**This is the structural problem, and it is bigger than the parser choice.**

Two facts collide:

1. **Docker on macOS cannot access MPS or MLX.** Containerised inference on this machine is
   CPU-only, for every parser in this evaluation.
2. **This repo's production ingestion path is a Docker worker** — `Dockerfile.mineru` bakes MinerU
   and its models into the image, `docker-compose.yml` runs `celery_worker` from it with a 12 GB
   cap and `MINERU_BAKED_INTO_IMAGE=1`.

Confirmed on this machine: **`mineru` is not on the host PATH.** Whatever ingestion has run here
went through the container — which means it has been running **CPU-only the entire time**, and the
1–2 min/10-page baseline reflects that, not this hardware's ceiling.

```text
TODAY                                    TO USE MLX / MPS
─────                                    ────────────────
compose celery_worker                    host celery_worker
  └─ Dockerfile.mineru                     └─ mineru / paddleocr on host PATH
      └─ MinerU, CPU-only  ⚠                   └─ MLX or MPS acceleration ✅
          (Docker can't reach MLX)
                                         ⚠ loses: baked models, 12 GB memory cap,
                                           restart:unless-stopped, autoheal,
                                           reproducible worker image
```

So the acceleration prize and the containerisation benefits are **mutually exclusive on macOS**.
Three options, and this plan does not pick one — it flags that the choice must be made
deliberately rather than discovered:

| Option | Gains | Loses |
| --- | --- | --- |
| Host worker | MLX/MPS acceleration for both parsers | Memory cap, autoheal, image reproducibility |
| Keep container | Current ops story intact | All acceleration; parser choice matters much less |
| Split: host extraction, container everything else | Acceleration where it counts | A second deployment mode to maintain and document |

⚠ **Measure arm A (§4) on the host, not in the container**, or the comparison is rigged against
MinerU — the container is CPU-only and `hybrid-engine` is exactly the backend Docker cannot
accelerate.

---

## 8. Failure catalog

| Failure | Detected by | Behavior | Recovery |
| --- | --- | --- | --- |
| PaddleOCR-VL mlx-vlm shape-incompatibility on M4 | Setup crash | Extraction unavailable | Reported by at least one user; budget a day for setup. Fall back to arm A |
| Candidate emits blocks but wrong `block_order` | M1 measurement | Reading order worse than MinerU | Decision rule row 3 — do not switch |
| Adapter maps `block_label` to the wrong `kind` | Chunk types visibly wrong in the reader | Headings render as text, etc. | Adapter unit test over a fixed fixture |
| Parser succeeds, chunker rejects the shape | `create_chunks_*` raises | Doc marked `failed` | ⚠ Existing behavior; the `Block` interface is what prevents it |
| Book routed to PaddleOCR-VL by mistake | OOM / very long run | Worker killed | Route on `doc_kind='book'` **and** page count, not either alone |
| Host worker loses the 12 GB cap | Host memory pressure | System-wide slowdown | §7 — an accepted cost of the host option |
| MinerU inline-formula toggle never tested | — | 82 lines of glyph-repair kept unnecessarily | §9 |

---

## 9. Known sharp edges

- ⚠ **Test MinerU's own fix before switching for M2.** The Unicode-glyph leakage is most likely
  *inline* formula text never routed to the MFR model at all — not the MFR model failing. MinerU
  3.x's hybrid backend added an **independent inline-formula toggle**. Flipping it is a
  five-minute experiment that could delete `_normalize_math_glyphs` + `_normalize_inline_math`
  (~82 lines) with **no parser change**. Do this first; it is the cheapest possible win in this
  entire document.
- ⚠ **MinerU is more actively maintained than assumed.** Current line is 3.4.x: 3.0 (relicensing,
  sliding-window long-document memory, thread-safe concurrent inference, `mineru` became an
  orchestration client over `mineru-api` with async `POST /tasks`), 3.1 (commercial-friendly
  license, MinerU2.5-Pro-2604-1.2B, PPTX/XLSX), 3.3 (`effort` parameter — medium is the new
  default, trading 0.13 accuracy points for 35–220 % speed by platform, but **medium does not
  support in-document image analysis**), 3.4 (PP-OCRv6, ~11 % better OCR accuracy). ⚠ That
  `effort` default matters directly: this app depends on figure extraction.
- **Two extractors means two failure profiles to learn.** The current MinerU-specific repair code
  represents accumulated debugging. A second parser starts that clock over. Routing by document
  length means both are live permanently — there is no "we switched and moved on."
- **`hybrid-engine` is untested here.** The evaluation compares against a MinerU
  configuration this repo has never run. A fair result may simply be "configure MinerU properly."
- **The perf baselines in [operations.md](../01-orientation/operations.md) say 32 GB.** Actual
  hardware is M4 Max / 48 GB, and the numbers were measured against a CPU-only container. Treat
  every figure there as a floor, not a benchmark.

---

## 10. Segmentation

| Seg | Scope | Done when |
| --- | --- | --- |
| ~~S0~~ | ✅ **DONE 2026-07-25 — see §0.** It ended the plan: the reading-order premise did not reproduce on MinerU 3.4.4. | Complete. |
| **S1** | The §4 measurement. Arms A and B over 20 real PDFs. No app changes. | M1 and M2 tables land in this doc with real numbers, and the §4 decision rule selects a row. |
| **S2** | `app/extraction/base.py` `Block` record + MinerU adapter refactor. Behavior-identical. | Existing papers re-chunk to byte-identical chunks via the new interface (`POST /papers/{id}/rechunk` is the harness). |
| **S3** | PaddleOCR-VL adapter + length-based routing + `documents.extractor` values extended. | A paper routes to PaddleOCR-VL, a book to MinerU, both produce correct typed chunks. |
| **S4** | If M1 passed: delete `reconstruct_reading_order` (task, 3 columns, endpoint, UI button). Resolve §7. | The subsystem is gone, and the host-vs-container decision is made and documented. |

**Not in scope:** replacing MinerU for books, adopting Docling, the `effort=medium` figure-analysis
regression (file it separately if S0 surfaces it), and any change to chunking semantics. This plan
adds an extractor and deletes one workaround. It does not redesign extraction.
