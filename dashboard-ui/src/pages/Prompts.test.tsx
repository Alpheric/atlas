import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../test/render';
import Prompts from './Prompts';
import * as api from '../lib/api';

vi.mock('../lib/api');

describe('Prompts page', () => {
  beforeEach(() => vi.resetAllMocks());

  it('renders prompt names and active version', async () => {
    vi.mocked(api.getPrompts).mockResolvedValue({
      data: [
        { name: 'self_critique', versions: 2, active_version: 2, updated_at: '2026-05-12T10:00:00Z' },
      ],
    });
    renderWithProviders(<Prompts />);
    expect(await screen.findByText('self_critique')).toBeInTheDocument();
    expect(screen.getByText('v2')).toBeInTheDocument();
  });

  it('shows empty state when no prompt overrides exist', async () => {
    vi.mocked(api.getPrompts).mockResolvedValue({ data: [] });
    renderWithProviders(<Prompts />);
    await waitFor(() =>
      expect(screen.getByText(/uses built-in code defaults/i)).toBeInTheDocument(),
    );
  });
});
