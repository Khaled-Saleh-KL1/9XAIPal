import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ModelCatalog } from '../api';
import { ModelPicker } from './ModelPicker';

const catalog: ModelCatalog = {
  default: 'gemma4:31b',
  models: [
    { name: 'gpt-oss:120b', is_cloud: true, size_bytes: 10, available: true, unavailable_reason: null },
    { name: 'gemma4:31b', is_cloud: false, size_bytes: 20, available: true, unavailable_reason: null },
    { name: 'meta/muse-glimmer-30b', is_cloud: true, size_bytes: 30, available: true, unavailable_reason: null },
    { name: 'glm-5.3-flash', is_cloud: true, size_bytes: 40, available: false, unavailable_reason: 'needs a paid Ollama plan' },
  ],
};

describe('ModelPicker', () => {
  it('shows Muse by its friendly name and places disabled unavailable models last', () => {
    const onChange = vi.fn();
    render(
      <ModelPicker
        catalog={catalog}
        model="gemma4:31b"
        onChange={onChange}
        title="Which model answers this note"
      />,
    );

    const select = screen.getByRole('combobox', { name: 'Which model answers this note' });
    const muse = within(select).getByRole('option', { name: 'Muse Glimmer 30B (NVIDIA)' });
    expect(muse).toHaveValue('meta/muse-glimmer-30b');

    const unavailable = within(select).getByRole('option', {
      name: 'GLM 5.3 Flash (needs a paid Ollama plan)',
    });
    expect(unavailable).toBeDisabled();
    expect(unavailable).toHaveAttribute('title', 'needs a paid Ollama plan');

    const options = [...select.querySelectorAll('option')];
    expect(options.indexOf(muse as HTMLOptionElement)).toBeLessThan(
      options.indexOf(unavailable as HTMLOptionElement),
    );
    fireEvent.change(select, { target: { value: 'meta/muse-glimmer-30b' } });
    expect(onChange).toHaveBeenCalledWith('meta/muse-glimmer-30b');
  });
});
