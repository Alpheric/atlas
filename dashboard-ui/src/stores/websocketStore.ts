import { create } from 'zustand';
import { useAuthStore } from './authStore';

type EventCallback = (data: any) => void;

interface WebSocketState {
  connected: boolean;
  ws: WebSocket | null;
  subscribers: Map<string, Set<EventCallback>>;
  connect: () => void;
  disconnect: () => void;
  subscribe: (eventType: string, cb: EventCallback) => () => void;
}

export const useWebSocketStore = create<WebSocketState>((set, get) => ({
  connected: false,
  ws: null,
  subscribers: new Map(),

  connect: () => {
    const state = get();
    if (state.ws) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Include auth token as query param — browsers can't send headers on WS upgrade
    const token = useAuthStore.getState().token;
    const tokenParam = token ? `?token=${encodeURIComponent(token)}` : '';
    const ws = new WebSocket(`${protocol}//${window.location.host}/admin/ws/live-feed${tokenParam}`);

    ws.onopen = () => set({ connected: true });

    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        const type = event.type || 'message';
        const subs = get().subscribers.get(type);
        subs?.forEach((cb) => cb(event));
        // Also notify '*' subscribers (catch-all)
        const allSubs = get().subscribers.get('*');
        allSubs?.forEach((cb) => cb(event));
      } catch {}
    };

    ws.onclose = () => {
      set({ connected: false, ws: null });
      // Auto-reconnect after 3s
      setTimeout(() => get().connect(), 3000);
    };

    ws.onerror = () => {};

    set({ ws });
  },

  disconnect: () => {
    const { ws } = get();
    ws?.close();
    set({ ws: null, connected: false });
  },

  subscribe: (eventType, cb) => {
    const { subscribers } = get();
    if (!subscribers.has(eventType)) {
      subscribers.set(eventType, new Set());
    }
    subscribers.get(eventType)!.add(cb);

    // Return unsubscribe function
    return () => {
      subscribers.get(eventType)?.delete(cb);
    };
  },
}));
