import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../test/render';
import Accounts from './Accounts';
import api from '../lib/api';

vi.mock('../lib/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

const mockApi = api as unknown as { get: ReturnType<typeof vi.fn> };

describe('Accounts page', () => {
  beforeEach(() => vi.resetAllMocks());

  it('renders accounts from the API via React Query', async () => {
    mockApi.get.mockResolvedValue({
      data: { data: [
        { id: 'a1', provider: 'anthropic', name: 'Claude Team #1', is_active: true, priority: 10, total_requests: 5, total_tokens: 1000 },
      ] },
    });
    renderWithProviders(<Accounts />);
    expect(await screen.findByText('Claude Team #1')).toBeInTheDocument();
    expect(screen.getByText('anthropic')).toBeInTheDocument();
  });

  it('shows the accounts table empty when API returns nothing', async () => {
    mockApi.get.mockResolvedValue({ data: { data: [] } });
    renderWithProviders(<Accounts />);
    await waitFor(() => expect(screen.getByText(/Provider Accounts/i)).toBeInTheDocument());
  });
});
