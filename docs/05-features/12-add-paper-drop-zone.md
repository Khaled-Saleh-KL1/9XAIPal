# Area 12 — Add-paper drop zone (feature 116)

> Part of the [feature catalogue](README.md). This entry covers the library’s PDF drop path and the file-selection step in the Add paper dialog.
>
> **Reflects code as of:** 2026-10-05 (`48cb8c6`).

---

## 116. Add paper: PDF drop and file selection

**What it does.** The library accepts PDF drops across the whole view. The **Add paper** button opens a centered chooser with its own PDF drop zone and file picker; the selected file is then assigned a reading kind before upload. The chooser also offers a separate URL-import path. ([`LibraryView.tsx`](../../frontend/src/views/LibraryView.tsx), [`App.tsx`](../../frontend/src/App.tsx))

**Where.** [`LibraryView.tsx`](../../frontend/src/views/LibraryView.tsx) owns the library-wide drop target and button; [`App.tsx`](../../frontend/src/App.tsx) owns `startUpload`, `UploadKindModal`, the hidden input, and the selected-file state; [`pdfFiles.ts`](../../frontend/src/lib/pdfFiles.ts) classifies candidate PDFs; [`Sheet.tsx`](../../frontend/src/motion/Sheet.tsx) and [`motion.css`](../../frontend/src/motion/motion.css) provide the centered dialog.

**How it works.**

1. The library’s root handles file drag events and shows a full-view **Drop to add to your library** prompt while a file drag is over it. Its **Add paper** button opens `UploadKindModal`. A valid library drop selects the first PDF, keeps the file for the kind chooser, and does not reopen the native picker. ([`LibraryView.tsx`](../../frontend/src/views/LibraryView.tsx), [`App.tsx`](../../frontend/src/App.tsx))
2. Inside the chooser, the labelled **Drop a PDF here, or browse** button is also a drop target. Clicking it opens a hidden file input with `accept="application/pdf,.pdf"`; dropping or selecting a file runs the same file-selection logic. A drag-depth counter keeps the hover state active while the pointer crosses children of the drop zone. ([`App.tsx`](../../frontend/src/App.tsx))
3. The client treats a file as PDF when its MIME type is `application/pdf` **or** its name ends in `.pdf`, case-insensitively. It preserves the input order, selects the first matching PDF, counts later PDFs as extras, and collects non-PDF files as rejected. This is a MIME/name check in the browser; it does not inspect the file’s byte signature there. ([`pdfFiles.ts`](../../frontend/src/lib/pdfFiles.ts), [`App.tsx`](../../frontend/src/App.tsx), [`LibraryView.tsx`](../../frontend/src/views/LibraryView.tsx))
4. If several files are dropped, the first valid PDF is retained. In the chooser, rejected filenames produce an alert; if there are extra PDFs, the alert says which PDF is being used and that only one file at a time is accepted. If there is no valid PDF, the chooser stays open, displays the rejection notice, and shakes the drop zone unless reduced motion is enabled. A retained PDF appears as a filename/size chip; **Remove file** clears the file and notice. ([`App.tsx`](../../frontend/src/App.tsx), [`LibraryView.tsx`](../../frontend/src/views/LibraryView.tsx), [`pdfFiles.ts`](../../frontend/src/lib/pdfFiles.ts))
5. After a drop or a browse from the drop zone, the file remains pending until **Book** or **Research paper** is chosen. Those buttons become **Upload as book** and **Upload as research paper**; choosing one closes the chooser and starts the upload. With no pending file, choosing either kind first opens the native picker and remembers that kind, so the selected file can proceed directly. ([`App.tsx`](../../frontend/src/App.tsx))
6. While a file is pending, URL actions are hidden and the dialog says to remove the file to paste a link. **Cancel** closes the chooser and clears the pending file and notice. The dialog is a labelled `role="dialog"` with `aria-modal="true"`; it opens centered, focuses the first kind choice, traps focus, and restores focus to the opener when it closes. ([`App.tsx`](../../frontend/src/App.tsx), [`Sheet.tsx`](../../frontend/src/motion/Sheet.tsx), [`motion.css`](../../frontend/src/motion/motion.css), [`useFocusTrap.ts`](../../frontend/src/motion/useFocusTrap.ts))

**See it.** In the library, choose **Add paper** to open the chooser. Drop a PDF, remove it, or drop a mix of PDFs and other files to see selection and rejection notices. Choose **Book** or **Research paper** to continue to upload. ([`LibraryView.tsx`](../../frontend/src/views/LibraryView.tsx), [`App.tsx`](../../frontend/src/App.tsx))
