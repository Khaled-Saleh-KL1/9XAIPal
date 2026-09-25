import { afterEach, describe, expect, it, vi } from 'vitest';
import { confirmArabicWritingStyle } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('confirmArabicWritingStyle', () => {
  it('posts the selected style to the existing document endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      paper_id: 'doc-1',
      job_id: 'job-1',
      status: 'arabic_ocr_queued',
      message: 'Arabic OCR was queued.',
    }), { status: 202, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await confirmArabicWritingStyle('doc-1', 'printed');

    expect(result.status).toBe('arabic_ocr_queued');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/papers/doc-1/arabic-writing-style',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ writing_style: 'printed' }),
      }),
    );
  });

  it('surfaces a useful message from a typed API error response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        error_code: 'arabic_confirmation_not_required',
        message: 'This document is not waiting for confirmation.',
      },
    }), { status: 409, headers: { 'Content-Type': 'application/json' } })));

    await expect(confirmArabicWritingStyle('doc-1', 'handwritten'))
      .rejects.toThrow('This document is not waiting for confirmation.');
  });
});
