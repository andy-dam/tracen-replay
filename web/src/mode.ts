// Whether this client runs inside the desktop application (no accounts, a
// Settings page, a window of its own) rather than in a browser. App.vue sets
// it once the service has said so.
import { ref } from "vue";
import { api } from "./api";

export const desktop = ref(false);

/**
 * Follows a link that needs a page of its own. A browser opens a new tab.
 * The desktop application's window cannot, on a Mac not at all, so there
 * the application shows the address in the system's browser instead.
 */
export function openExternal(event: MouseEvent, href: string) {
  if (!desktop.value) return;
  event.preventDefault();
  api.desktopOpen(new URL(href, window.location.href).href).catch(() => {
    // The link stays on the page; nothing else to do.
  });
}
