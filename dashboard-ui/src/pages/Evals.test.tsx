import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../test/render';
import Evals from './Evals';
import * as api from '../lib/api';

vi.mock('../lib/api');

describe('Evals page', () => {
  beforeEach(() => vi.resetAllMocks());

  it('renders datasets and runs tabs with data', async () => {
    vi.mocked(api.getEvalDatasets).mockResolvedValue({
      data: [{ id: 'd1', name: 'code-eval', task_type: 'code', item_count: 15, created_at: '2026-05-12T10:00:00Z' }],
    });
    vi.mocked(api.getEvalRuns).mockResolvedValue({
      data: [{ id: 'r1', model: 'gemini-2.5-flash', status: 'completed', item_count: 15, avg_heuristic: 0.6, avg_judge: 0.88, avg_latency_ms: 3000, created_at: '2026-05-12T10:00:00Z' }],
    });
    renderWithProviders(<Evals />);
    // Dataset name appears (datasets tab is default)
    expect(await screen.findByText('code-eval')).toBeInTheDocument();
    expect(screen.getByText(/Datasets \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Runs \(1\)/)).toBeInTheDocument();
  });

  it('disables Run Eval when there are no datasets', async () => {
    vi.mocked(api.getEvalDatasets).mockResolvedValue({ data: [] });
    vi.mocked(api.getEvalRuns).mockResolvedValue({ data: [] });
    renderWithProviders(<Evals />);
    const runBtn = await screen.findByRole('button', { name: /Run Eval/i });
    expect(runBtn).toBeDisabled();
  });
});
