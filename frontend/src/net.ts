import type { ServerMessage } from './types';

type Handler = (msg: ServerMessage) => void;

export class Net {
  private ws?: WebSocket;
  private handlers: Handler[] = [];
  private retry = 0;

  on(handler: Handler) {
    this.handlers.push(handler);
  }

  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    this.ws = new WebSocket(`${proto}://${location.host}/ws`);

    this.ws.onopen = () => {
      this.retry = 0;
      // The server ignores inbound frames, but a heartbeat keeps proxies honest.
      setInterval(() => this.ws?.readyState === WebSocket.OPEN && this.ws.send('ping'), 25000);
    };
    this.ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data) as ServerMessage;
      for (const h of this.handlers) h(msg);
    };
    this.ws.onclose = () => {
      this.retry = Math.min(this.retry + 1, 6);
      setTimeout(() => this.connect(), 500 * 2 ** this.retry);
    };
  }
}

async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  world: () => fetch('/api/world').then((r) => r.json()),
  agent: (id: string) => fetch(`/api/agent/${id}`).then((r) => r.json()),
  relationships: () => fetch('/api/relationships').then((r) => r.json()),
  usage: () => fetch('/api/usage').then((r) => r.json()),
  event: (text: string, targets?: string[]) => post('/api/event', { text, targets }),
  ask: (agent_id: string, text: string) =>
    post<{ reply: string }>('/api/ask', { agent_id, text }),
  control: (action: string, seconds_per_tick?: number) =>
    post<{ paused: boolean; seconds_per_tick: number }>('/api/control', {
      action,
      seconds_per_tick,
    }),
};
