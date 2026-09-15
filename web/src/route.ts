// Moving between turns changes the hash so a turn can be linked to, but it
// is one page: the browser's Back button should leave the report, not retrace
// every turn that was clicked. replaceHash swaps the current history entry
// and tells the router, which listens for hashchange.
export function replaceHash(hash: string): void {
  if (window.location.hash === hash) return;
  window.history.replaceState(window.history.state, "", hash);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}
