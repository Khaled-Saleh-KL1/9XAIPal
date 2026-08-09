#!/usr/bin/env python3
"""Score a MinerU content_list.json for the three defects the chunker repairs.

Used to decide whether ~280 lines of repair heuristics in
``backend/app/extraction/chunker.py`` are still earning their keep against the
installed MinerU version. See docs/plans/mineru-heuristic-removal.md.

    python3 scripts/analyze_content_list.py <content_list.json> [more...]
    python3 scripts/analyze_content_list.py --summary out/**/*_content_list.json

Metrics
  LAYOUT  single vs two column, from bbox left-edge clustering.
          A reading-order result on a single-column paper proves nothing.
  M1      reading order — numbered section headings must be monotonic.
  M2      equation integrity — orphan "(N)" labels and mid-construct splits.
          Guards: _stitch_split_equations + fragment helpers (~200 lines).
          ⚠ `adj` (consecutive equation blocks) is INFORMATIONAL ONLY, not a
          defect signal: numbered groups like (12a)(12b)(12c) and multi-step
          derivations are legitimately adjacent. Measured 2026-07-26 — every
          one of 143 adjacent pairs across 28 papers was a false positive.
          Judge fragmentation on `orph` and `tail` alone.
  M3      unicode-vs-LaTeX — math glyphs that should be LaTeX commands.
          Guards: _normalize_math_glyphs + _normalize_inline_math (~82 lines).
          ⚠ Counted ONLY inside equation blocks and inline $...$ spans — that
          is the population _normalize_math_glyphs actually sees (chunker.py
          L331, L421-436, L1064). Greek letters in prose ("ΛCDM", "3.9σ",
          "3×3 conv") are terminology, NOT broken math, and must not be
          counted: an earlier version of this script did, and nearly produced
          a wrong delete recommendation.

Exit code 0 always; read the output. --summary prints one row per file.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

# Glyphs that indicate math was NOT routed to the formula model. Deliberately
# excludes bullets (•), dashes, and quotes — those are ordinary prose.
UNICODE_MATH = "√×÷∑∏∫≤≥≠≈≡∞∂∇⊕⊗⊆⊂∈∉∪∩αβγδεζηθκλμνξπρστφχψωΓΔΘΛΞΠΣΦΨΩ"
ORPHAN_LABEL = re.compile(r"^\(\s*\d+[a-z]?\s*\)$")
SECTION_NUM = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
OPEN_TAIL = re.compile(r"(?:[+\-=*/^_,]|\\\\|\\left|\\begin\{[a-z]*\}?)\s*$")
INLINE_MATH = re.compile(r"\$([^$\n]{1,400})\$")


def _text(b: dict) -> str:
    for k in ("text", "content", "latex", "md"):
        v = b.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _seckey(s: str) -> tuple:
    return tuple(int(x) for x in s.split("."))


def analyze(path: Path) -> dict:
    blocks = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(blocks, dict):
        blocks = blocks.get("content_list") or blocks.get("items") or []

    # ---- LAYOUT: cluster text-block left edges -------------------------------
    x0s = [b["bbox"][0] for b in blocks if b.get("bbox") and b.get("type") == "text"]
    hist = Counter(round(x / 40) * 40 for x in x0s)
    top = sorted(hist.items(), key=lambda kv: -kv[1])[:3]
    major = [c for _, c in top if x0s and c >= len(x0s) * 0.15]
    layout = "two-column" if len(major) >= 2 else "single-column"

    # ---- M1: numbered headings monotonic? -----------------------------------
    # NOTE: MinerU marks headings with text_level 1 *or* 2 (title vs section).
    # Checking only level 1 finds nothing — that bug produced a false pass.
    heads = [(i, _text(b).strip()) for i, b in enumerate(blocks) if b.get("text_level")]
    numbered = [(i, m.group(1)) for i, t in heads if (m := SECTION_NUM.match(t))]
    inversions = [
        (numbered[j - 1], numbered[j])
        for j in range(1, len(numbered))
        if _seckey(numbered[j][1]) < _seckey(numbered[j - 1][1])
    ]

    # ---- M2: equation integrity ---------------------------------------------
    eq = [i for i, b in enumerate(blocks) if "equation" in str(b.get("type", "")).lower()]
    orphans = [i for i, b in enumerate(blocks) if ORPHAN_LABEL.match(_text(b).strip())]
    adjacent = sum(1 for a, b_ in zip(eq, eq[1:]) if b_ - a == 1)
    open_tail = [i for i in eq if OPEN_TAIL.search(_text(blocks[i]).strip())]

    # ---- M3: unicode math INSIDE math spans ---------------------------------
    # Only equation bodies and inline $...$ reach _normalize_math_glyphs.
    # Glyphs in prose are terminology and are deliberately not counted.
    leaks = []
    for i, b in enumerate(blocks):
        t = str(b.get("type", "")).lower()
        s = _text(b)
        if "equation" in t:
            hits = [c for c in s if c in UNICODE_MATH]
            if hits:
                leaks.append((i, len(hits), f"[eq] {s.strip()[:66]}"))
        elif "table" not in t:
            for m in INLINE_MATH.finditer(s):
                hits = [c for c in m.group(1) if c in UNICODE_MATH]
                if hits:
                    leaks.append((i, len(hits), f"[inline] {m.group(0)[:60]}"))

    return {
        "file": path.name,
        "blocks": len(blocks),
        "layout": layout,
        "clusters": top,
        "headings": len(heads),
        "numbered": len(numbered),
        "inversions": inversions,
        "sequence": [n[1] for n in numbered],
        "equations": len(eq),
        "adjacent": adjacent,
        "open_tail": len(open_tail),
        "orphans": orphans,
        "leaks": leaks,
        "types": dict(Counter(b.get("type") for b in blocks)),
    }


def report(r: dict) -> None:
    print(f"\n{'=' * 74}\n  {r['file']}  ({r['blocks']} blocks, {r['layout']})\n{'=' * 74}")
    print(f"  left-edge clusters: {r['clusters']}")
    if r["layout"] == "single-column":
        print("  ⚠ SINGLE-COLUMN — a reading-order pass here proves nothing about two-column.")
    print(f"\n  M1 reading order   headings={r['headings']} numbered={r['numbered']}")
    print(f"     INVERSIONS: {len(r['inversions'])}"
          + ("   <-- WRONG ORDER" if r["inversions"] else "   <-- monotonic OK"))
    for a, b in r["inversions"][:8]:
        print(f"        block{a[0]} '{a[1]}' -> block{b[0]} '{b[1]}'")
    if r["sequence"]:
        print(f"     sequence: {' '.join(r['sequence'])}")
    print(f"\n  M2 equations       n={r['equations']} adjacent={r['adjacent']} "
          f"open_tail={r['open_tail']} ORPHAN_LABELS={len(r['orphans'])}")
    print(f"     (adj is informational — consecutive equations are normal, not a defect)")
    print(f"     -> _stitch_split_equations "
          + ("NOT needed here" if not (r["orphans"] or r["open_tail"])
             else "IS EARNING ITS KEEP"))
    print(f"\n  M3 unicode math    blocks={len(r['leaks'])} "
          f"glyphs={sum(n for _, n, _ in r['leaks'])}")
    print(f"     -> _normalize_math_glyphs "
          + ("NOT needed here" if not r["leaks"] else "IS EARNING ITS KEEP"))
    for i, n, s in r["leaks"][:5]:
        print(f"        block{i} ({n}): {s}")
    print(f"\n  types: {r['types']}")


def main(argv: list[str]) -> int:
    summary = "--summary" in argv
    paths = [Path(a) for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 0
    rows = []
    for p in paths:
        try:
            r = analyze(p)
        except Exception as e:  # a malformed file must not abort a 20-paper run
            print(f"!! {p}: {e}")
            continue
        rows.append(r)
        if not summary:
            report(r)

    print(f"\n{'=' * 92}\n  SUMMARY — {len(rows)} file(s)\n{'=' * 92}")
    print(f"  {'file':<34} {'layout':<14} {'inv':>4} {'orph':>5} {'adj':>4} {'tail':>5} {'glyph':>6}")
    for r in rows:
        print(f"  {r['file'][:34]:<34} {r['layout']:<14} {len(r['inversions']):>4} "
              f"{len(r['orphans']):>5} {r['adjacent']:>4} {r['open_tail']:>5} "
              f"{sum(n for _, n, _ in r['leaks']):>6}")
    two_col = [r for r in rows if r["layout"] == "two-column"]
    print(f"\n  two-column papers: {len(two_col)}/{len(rows)}"
          + ("   ⚠ NEED >=15 TWO-COLUMN FOR A VALID VERDICT" if len(two_col) < 15 else "   ✓"))
    tot = lambda k: sum(len(r[k]) if isinstance(r[k], list) else r[k] for r in rows)
    print(f"  totals: inversions={tot('inversions')} orphans={tot('orphans')} "
          f"adjacent={tot('adjacent')} open_tail={tot('open_tail')}")
    print("\n  DELETE GATE: remove a heuristic only if its column is 0 across ALL rows")
    print("               AND >=15 rows are two-column.")
    print("               `adj` is INFORMATIONAL — never gate on it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
