# Area 11 — Reasoning UI (feature 115)

> Part of the [feature catalogue](README.md). This entry describes how agent steps are grouped and shown while an answer is being produced.
>
> **Reflects code as of:** 2026-10-05 (`48cb8c6`).

---

## 115. Agent reasoning rows and live status

**What it does.** `Reasoning` presents agent activity as collapsible round rows, each titled from the round’s `THINK` summary when one is available. While a request is live, a status line pairs the current activity text with the animated 9XAIPal mark. ([`AgentTrail.tsx`](../../frontend/src/views/AgentTrail.tsx), [`ThinkingMark.tsx`](../../frontend/src/components/ThinkingMark.tsx))

**Where.** [`AgentTrail.tsx`](../../frontend/src/views/AgentTrail.tsx) exports `Reasoning` (and the compatibility alias `AgentTrail`); [`api.ts`](../../frontend/src/api.ts) defines `AgentStep`; [`ThinkingMark.tsx`](../../frontend/src/components/ThinkingMark.tsx) draws the live mark. The UI is used in the reader chat, study chat, and note answers. ([`ChatPane.tsx`](../../frontend/src/views/ChatPane.tsx), [`StudyChat.tsx`](../../frontend/src/views/StudyChat.tsx), [`NoteCard.tsx`](../../frontend/src/views/NoteCard.tsx))

**How it works.**

1. Each `AgentStep` carries a 1-based round number `n`, a tool, state, label, result, citations, sources, and an optional `think` string described in the type as the model’s one-line reason on the first call of a round. `Reasoning` groups steps by `n`, sorts rounds numerically, and renders one collapsible row for each visible round. ([`api.ts`](../../frontend/src/api.ts), [`AgentTrail.tsx`](../../frontend/src/views/AgentTrail.tsx))
2. The row label uses the first step’s nonempty `think` summary, falling back to **See reasoning**. Expanding it lists that round’s individual tool steps. Each row shows the tool glyph and label; a running step shows a working indicator, while a completed step can show its result. Page citations can be clicked to call `onJump`; web sources link to their host. ([`AgentTrail.tsx`](../../frontend/src/views/AgentTrail.tsx), [`pageMap.ts`](../../frontend/src/lib/pageMap.ts))
3. Up to six rounds are shown initially. If there are more, **+N more steps** reveals the remaining round rows. Below them, an aggregate summary reports paper steps, web steps, notes pinned, and remembered items when those counts are nonzero. ([`AgentTrail.tsx`](../../frontend/src/views/AgentTrail.tsx))
4. While `live` is true, the status row is present. It says **Writing the answer…** after answer streaming begins, changing to **Almost there…** after the status timer; otherwise it names the running tool activity or says **Thinking…** when no step is running. Status updates use a polite, atomic live region. ([`AgentTrail.tsx`](../../frontend/src/views/AgentTrail.tsx))
5. The live row’s `ThinkingMark` is decorative (`aria-hidden`). Its square scales and turns, the “9” changes opacity, and a dot moves around the mark in a repeating path. These loops run only while the mark is in view and the document is visible; they are omitted when reduced motion is requested. ([`ThinkingMark.tsx`](../../frontend/src/components/ThinkingMark.tsx), [`usePauseWhenHidden.ts`](../../frontend/src/motion/usePauseWhenHidden.ts))

**See it.** Ask a question in the reader or study chat while agent steps arrive. Expand a round to inspect its tool steps and select a page citation to jump to its source. The reader and study chat also render the same component for completed answers when stored agent steps are present. ([`ChatPane.tsx`](../../frontend/src/views/ChatPane.tsx), [`StudyChat.tsx`](../../frontend/src/views/StudyChat.tsx), [`NoteCard.tsx`](../../frontend/src/views/NoteCard.tsx))
