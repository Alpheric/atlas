import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../test/render';
import CostOps from './CostOps';
import * as api from '../lib/api';

vi.mock('../lib/api');

describe('Cost & Ops page', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.getCostByWorkspace).mockResolvedValue({
      total_cost_usd: 107.07,
      data: [{ workspace_id: null, workspace_name: '(unattributed)', requests: 100, cost_usd: 107.07, savings_usd: 0, budget: null }],
    });
    vi.mocked(api.getCostByKey).mockResolvedValue({
      data: [{ api_key_hash: 'abc', label: 'notifire', requests: 50, total_tokens: 1000, cost_usd: 43.8, errors: 0 }],
    });
    vi.mocked(api.getAnomalies).mockResolvedValue({ enabled: true, data: [] });
  });

  it('renders cost tables and anomaly section', async () => {
    renderWithProviders(<CostOps />);
    expect(await screen.findByText('notifire')).toBeInTheDocument();
    expect(screen.getByText('(unattributed)')).toBeInTheDocument();
    expect(screen.getByText(/No anomalies detected/i)).toBeInTheDocument();
  });

  it('runs a routing projection and shows savings', async () => {
    vi.mocked(api.runRoutingReplay).mockResolvedValue({
      routes_changed: 720, decisions_evaluated: 2699,
      actual_cost_usd: 107.07, projected_cost_usd: 39.55, projected_savings_usd: 67.52,
    });
    renderWithProviders(<CostOps />);
    await screen.findByText('notifire');
    await userEvent.click(screen.getByRole('button', { name: /Project/i }));
    // Unique projected-savings value from the mocked replay result.
    expect(await screen.findByText('$67.52')).toBeInTheDocument();
    expect(api.runRoutingReplay).toHaveBeenCalled();
  });
});
