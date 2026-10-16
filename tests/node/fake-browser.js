/**
 * Deterministic stand-in for BrowserManager: serves canned responses per URL, records every
 * navigation, and never touches the network or a real browser.
 */

/** A route is `{ status, headers, body }` or `{ error }` (thrown by `goto`). */
export function createFakeBrowser(routes = {}) {
  const state = { visited: [], openContexts: 0, closedContexts: 0, cookies: [], routed: 0 };

  const newContext = async () => {
    state.openContexts++;
    let current = null;
    return {
      addCookies: async (cookies) => {
        state.cookies.push(...cookies);
      },
      newPage: async () => ({
        route: async () => {
          state.routed++;
        },
        goto: async (url) => {
          state.visited.push(url);
          const route = routes[url] ?? { status: 404, body: "" };
          if (route.error) throw route.error;
          current = route;
          return {
            status: () => route.status ?? 200,
            headers: () => ({ "content-type": "text/html", ...route.headers }),
            text: async () => route.body ?? "",
          };
        },
        waitForLoadState: async () => {},
        content: async () => current?.body ?? "",
      }),
      close: async () => {
        state.closedContexts++;
      },
    };
  };

  return { state, get: async () => ({ newContext }), close: async () => {} };
}

export const page = (body, title = "Page") =>
  `<html><head><title>${title}</title></head><body>${body}</body></html>`;
