# Library Cover Toolbar Clearance Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Keep the hinged paper cover below the library toolbar divider while it opens on hover.

**Architecture:** Keep the current left-hinge rotations and remove the upward translation. Increase only the cover stage perspective so the projected cover remains below the divider at the grid's 24px top inset. Verify actual cover, card, and divider rectangles in the fixture at desktop and mobile widths, including a scrolled row.

**Tech Stack:** React, TypeScript, Motion, Vitest, Vite, Playwright browser checks.

**Spec:** /private/tmp/claude-501/-Users-khaled-saleh-kl1-MyStuff-All-Programming-Files-9XAIPal-VPS/0df7da94-8918-4b81-8cdc-75713469848e/scratchpad/codex/spec-L2d.md

## Global Constraints

- Never push, use SSH, or contact production.
- Start the fixture with node /private/tmp/claude-501/-Users-khaled-saleh-kl1-MyStuff-All-Programming-Files-9XAIPal-VPS/0df7da94-8918-4b81-8cdc-75713469848e/scratchpad/fe/fixture.mjs on 127.0.0.1:8019.
- Start Vite with VITE_API_TARGET=http://127.0.0.1:8019 npx vite --port 5195.
- npx vitest run must pass twice; npm run build must pass.
- Stop both local servers when finished.
- Write viewport measurements to /private/tmp/claude-501/-Users-khaled-saleh-kl1-MyStuff-All-Programming-Files-9XAIPal-VPS/0df7da94-8918-4b81-8cdc-75713469848e/scratchpad/codex/report-L2d.md.
- Commit locally with a message ending in Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>.

## Review Focus

- At 1440x900, the first hovered card must have its cover top below the toolbar divider; verify card, cover, and main rectangles in the fixture.
- At 1440x900, a hovered card in a scrolled row aligned to main.top + 24px must keep its cover below the divider; repeat the rectangle check after scrolling.
- At 360x900, the first hovered card must keep its cover below the toolbar divider; verify the mobile rectangles.
- At 360x900, a hovered card in a scrolled row aligned to main.top + 24px must keep its cover below the divider; repeat the rectangle check after scrolling.
- Reduced-motion preference must suppress the cover hover transform; assert that the cover's whileHover target is undefined in the existing reduced-motion test.

---

### Task 1: Keep the hinged cover below the toolbar

**Files:**
- Modify: frontend/src/views/LibraryView.tsx
- Modify: frontend/src/index.css
- Test: frontend/src/views/LibraryView.motion.test.tsx
- Create: /private/tmp/claude-501/-Users-khaled-saleh-kl1-MyStuff-All-Programming-Files-9XAIPal-VPS/0df7da94-8918-4b81-8cdc-75713469848e/scratchpad/codex/report-L2d.md

**Interfaces:**
- Consumes: PaperCard's paper-cover-motion Motion target and paper-cover-stage CSS perspective.
- Produces: Hover retains rotateY -30deg and rotateZ -3deg, applies no negative y translation, and keeps the transformed cover top below main.top at both tested viewport widths and row positions.

- [x] **Step 1: Add a failing hover-target regression test**

Capture the paper-cover-motion props in the existing test mock, then add a test that asserts the hover target retains the two opening rotations and has no upward y translation:

    const hoverTarget = mocks.paperMotionProps.get('cover-hover')?.whileHover as {
      rotateY?: number;
      rotateZ?: number;
      y?: number;
    };
    expect(hoverTarget.rotateY).toBe(-30);
    expect(hoverTarget.rotateZ).toBe(-3);
    expect(hoverTarget.y ?? 0).toBeGreaterThanOrEqual(0);

- [x] **Step 2: Run the focused test and confirm RED**

Run from frontend: npx vitest run src/views/LibraryView.motion.test.tsx -t "keeps the hinged cover from lifting toward the toolbar"

Expected: FAIL because the current Motion target sets y to -8.

- [x] **Step 3: Apply the minimal motion and perspective correction**

Remove y: -8 from PaperCard's whileHover object. Change only .paper-cover-stage perspective from 1100px to 5000px; retain rotateY -30deg, rotateZ -3deg, and the left-center transform origin.

- [x] **Step 4: Run the focused motion test and confirm GREEN**

Run from frontend: npx vitest run src/views/LibraryView.motion.test.tsx

Expected: all LibraryView motion tests pass, including the cover's reduced-motion behavior.

- [x] **Step 5: Verify all four browser geometry cases**

With the local fixture and Vite servers running, set the browser viewport to 1440x900 and 360x900. At each width, measure the first card and its .paper-cover-motion before and during hover. Then set main scroll behavior to auto, align .paper-card at index 4 to 24px below main.top, hover its cover, and measure again. In every case, confirm hovered cover.top > main.top. Record viewport, card top, cover top, divider top, and clearance in the report.

- [x] **Step 6: Run the full suite twice and build**

Run from frontend, as separate commands:

    npx vitest run
    npx vitest run
    npm run build

Expected: both full Vitest runs and the TypeScript/Vite build exit successfully.

- [x] **Step 7: Write the measurement report, stop both servers, and commit**

Write the four post-fix geometry measurements and the validation results to report-L2d.md. Stop the fixture and Vite processes. Commit the plan, test, implementation, and report only where repository-local; the external report stays at its required path. End the commit message with the required Co-Authored-By trailer.
