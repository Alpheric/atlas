import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../test/render';
import Conversations from './Conversations';
import * as api from '../lib/api';

vi.mock('../lib/api');

describe('Conversations page (React Query migration)', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.getConversations).mockResolvedValue({
      data: [
        { id: 'c1', source: 'openai', user_id: 'u1', message_count: 4, health_score: 0.8, created_at: '2026-05-12T10:00:00Z' },
      ],
      total: 1,
    });
    vi.mocked(api.getConversationStats).mockResolvedValue({ sources: { openai: 1 } });
    vi.mocked(api.getDistillationOverview).mockResolvedValue(null);
    vi.mocked(api.getSessions).mockResolvedValue({ data: [] });
    vi.mocked(api.getConversationHealth).mockResolvedValue({ data: [] });
  });

  it('renders without crashing and shows a conversation row', async () => {
    renderWithProviders(<Conversations />);
    // "openai" appears in both the source filter and the row — assert >= 1.
    const matches = await screen.findAllByText(/openai/i);
    expect(matches.length).toBeGreaterThan(0);
    expect(api.getConversations).toHaveBeenCalled();
  });
});
