import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../test/render';
import Routing from './Routing';
import * as api from '../lib/api';

vi.mock('../lib/api');

describe('Routing page', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.getRoutingDecisions).mockResolvedValue({
      data: [
        { id: 'd1', provider: 'claude-cli', model: 'atlas-code', task_type: 'code', latency_ms: 1200, cost_usd: 0.01, prompt_tokens: 100, completion_tokens: 50, is_local: false, created_at: '2026-05-12T10:00:00Z' },
      ],
    });
    vi.mocked(api.getModelLeaderboard).mockResolvedValue({ data: [] });
    vi.mocked(api.getDistillationOverview).mockResolvedValue(null);
    vi.mocked(api.getOverview).mockResolvedValue(null);
  });

  it('renders the routing page with decisions from React Query', async () => {
    renderWithProviders(<Routing />);
    expect(await screen.findByText('Routing Intelligence')).toBeInTheDocument();
    // Decision count badge reflects the single mocked decision
    expect(await screen.findByText('Recent Routing Decisions')).toBeInTheDocument();
  });
});
