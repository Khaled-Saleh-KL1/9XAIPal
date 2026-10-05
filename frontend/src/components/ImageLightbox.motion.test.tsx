import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { MotionRoot } from '../motion';
import { ImageLightbox } from './ImageLightbox';

describe('ImageLightbox motion', () => {
  it('keeps the backdrop through the open state and exits after Escape', async () => {
    const user = userEvent.setup();
    render(
      <MotionRoot>
        <ImageLightbox />
        <img data-testid="large-image" src="/figure.png" alt="A plotted figure" />
      </MotionRoot>,
    );
    const image = screen.getByTestId('large-image');
    Object.defineProperty(image, 'naturalWidth', { configurable: true, value: 120 });
    fireEvent.click(image);

    const backdrop = await screen.findByRole('dialog', { name: 'A plotted figure' });
    expect(backdrop).toHaveClass('lightbox-backdrop');
    expect(backdrop).toHaveStyle({ opacity: '0' });
    await user.keyboard('{Escape}');
    await waitFor(() => expect(document.querySelector('.lightbox-backdrop')).toBeNull());
  });
});
