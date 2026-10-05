import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PaperCover } from './PaperCover';

describe('PaperCover generated article covers', () => {
  it('adds the cover version to the image URL and retries after it changes', async () => {
    const props = {
      paperId: 'article-123',
      title: 'A study of reefs',
      ready: true,
      coverVersion: null as number | null,
    };
    const view = render(<PaperCover {...props} />);

    const initialImage = screen.getByRole('img');
    expect(initialImage.getAttribute('src')).toBe('/api/v1/papers/article-123/cover');
    fireEvent.error(initialImage);
    expect(screen.queryByRole('img')).toBeNull();

    view.rerender(<PaperCover {...props} coverVersion={1791211800} />);

    const refreshedImage = await screen.findByRole('img');
    expect(refreshedImage.getAttribute('src')).toBe(
      '/api/v1/papers/article-123/cover?v=1791211800',
    );
  });
});
