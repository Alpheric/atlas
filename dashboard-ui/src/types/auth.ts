export type Role = 'admin' | 'operator' | 'viewer';

export interface User {
  id: string;
  username: string;
  role: Role;
  is_active: boolean;
}

export interface LoginResponse {
  token: string;
  refresh_token: string;
  user: User;
}

export interface Permission {
  page: string;
  actions: ('read' | 'write' | 'delete' | 'export')[];
}
