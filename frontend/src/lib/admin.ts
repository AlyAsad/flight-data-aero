import { useSyncExternalStore } from "react";

// Admin password for a deployment with ADMIN_PASSWORD set. Kept for the browser
// session only, and sent as X-Admin-Password on protected requests.
const KEY = "aero-admin-password";
const listeners = new Set<() => void>();
let memory: string | null = null;

function read(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return memory;
  }
}

export function getAdminPassword(): string | null {
  return memory ?? read();
}

export function setAdminPassword(value: string | null): void {
  memory = value;
  try {
    if (value) sessionStorage.setItem(KEY, value);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: memory only */
  }
  listeners.forEach((l) => l());
}

export function useAdminPassword(): string | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    getAdminPassword,
    () => null,
  );
}
