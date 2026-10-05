import { describe, expect, it } from 'vitest';
import { isPdfFile, pickFirstPdf } from './pdfFiles';

const file = (name: string, type = '') => new File(['sample'], name, { type });

describe('PDF file selection', () => {
  it('accepts a PDF by MIME type', () => {
    expect(isPdfFile(file('notes', 'application/pdf'))).toBe(true);
  });

  it('accepts a PDF extension with an empty MIME type', () => {
    expect(isPdfFile(file('notes.pdf'))).toBe(true);
  });

  it('accepts an uppercase PDF extension', () => {
    expect(isPdfFile(file('NOTES.PDF'))).toBe(true);
  });

  it('rejects a non-PDF document', () => {
    expect(isPdfFile(file('notes.docx'))).toBe(false);
  });

  it('chooses the first PDF and counts rejected files and extra PDFs', () => {
    const firstPdf = file('first.pdf');
    const rejectedDoc = file('notes.docx');
    const secondPdf = file('second.PDF');
    const rejectedText = file('notes.txt');

    expect(pickFirstPdf([rejectedDoc, firstPdf, secondPdf, rejectedText])).toEqual({
      pdf: firstPdf,
      rejected: [rejectedDoc, rejectedText],
      extraPdfs: 1,
    });
  });

  it('returns no PDF when every file is rejected', () => {
    const document = file('notes.docx');
    expect(pickFirstPdf([document])).toEqual({ pdf: null, rejected: [document], extraPdfs: 0 });
  });
});
