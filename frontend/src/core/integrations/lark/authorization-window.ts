type AuthorizationWindow = {
  location: Pick<Location, "replace">;
  opener: unknown;
};

/**
 * Hand a synchronously pre-opened blank tab to an asynchronous OAuth flow.
 *
 * Detaching `opener` before navigation moves Chromium's blank tab out of the
 * opener's browsing-context group. A later parent-side `location` assignment
 * then throws `SecurityError`, leaving the user on a stale authorization page.
 * Schedule the trusted navigation first, then remove the reverse-tabnabbing
 * handle while the blank document is still same-origin.
 */
export function handOffAuthorizationWindow(
  browserWindow: AuthorizationWindow,
  url: string,
) {
  browserWindow.location.replace(url);
  browserWindow.opener = null;
}
