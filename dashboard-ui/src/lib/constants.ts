import type { Role } from '../types/auth';

export const ROLE_PAGE_ACCESS: Record<string, Role[]> = {
  '/': ['admin', 'operator', 'viewer'],
  '/conversations': ['admin', 'operator', 'viewer'],
  '/routing': ['admin', 'operator', 'viewer'],
  '/providers': ['admin', 'operator', 'viewer'],
  '/users': ['admin'],
  '/accounts': ['admin'],
  '/models': ['admin', 'operator', 'viewer'],
  '/training': ['admin', 'operator', 'viewer'],
  '/analytics': ['admin', 'operator', 'viewer'],
  '/import': ['admin', 'operator'],
  '/settings': ['admin'],
};

export const ROLE_WRITE_ACCESS: Record<string, Role[]> = {
  providers_refresh: ['admin', 'operator'],
  training_create: ['admin', 'operator'],
  training_cancel: ['admin'],
  import_trigger: ['admin', 'operator'],
  settings_save: ['admin'],
  feedback_submit: ['admin', 'operator'],
};

export const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

export const DATE_PRESETS = [
  { label: 'Today', value: 'today' },
  { label: 'Last 7 days', value: '7d' },
  { label: 'Last 30 days', value: '30d' },
  { label: 'Last 90 days', value: '90d' },
] as const;
