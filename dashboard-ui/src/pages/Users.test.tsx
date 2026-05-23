import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../test/render';
import Users from './Users';
import * as api from '../lib/api';

vi.mock('../lib/api');

describe('Users page (React Query migration)', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.getUsers).mockResolvedValue({
      data: [
        {
          id: 'u1', name: 'Alice', email: 'alice@example.com', role: 'admin',
          is_active: true, api_keys: [],
          usage: { total_requests: 0, prompt_tokens: 0, completion_tokens: 0, cost_usd: 0 },
        },
      ],
    });
  });

  it('renders without crashing and lists a user', async () => {
    renderWithProviders(<Users />);
    expect(await screen.findByText('Alice')).toBeInTheDocument();
    expect(api.getUsers).toHaveBeenCalled();
  });
});
