import { writable } from 'svelte/store';

export interface Toast {
  id: number;
  type: 'success' | 'error' | 'info';
  message: string;
}

const { subscribe, update } = writable<Toast[]>([]);

let nextId = 0;

function add(type: Toast['type'], message: string, duration = 4000) {
  const id = nextId++;
  update((all) => [...all, { id, type, message }]);
  if (duration > 0) {
    setTimeout(() => dismiss(id), duration);
  }
}

function dismiss(id: number) {
  update((all) => all.filter((t) => t.id !== id));
}

export const toasts = {
  subscribe,
  success: (msg: string, duration = 4000) => add('success', msg, duration),
  error: (msg: string, duration = 6000) => add('error', msg, duration),
  info: (msg: string, duration = 4000) => add('info', msg, duration),
  dismiss,
};
