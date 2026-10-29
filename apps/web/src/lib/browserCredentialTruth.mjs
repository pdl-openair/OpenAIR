/**
 * Browser Credential Truth (B0): resolve API auth without a hard-coded dev token.
 * Priority: stored login credential, then explicit VITE_OPENAIR_AUTH_TOKEN, else empty.
 */

/**
 * @param {string | null | undefined} storedToken
 * @param {string | null | undefined} envToken
 * @returns {string}
 */
export function resolveBrowserAuthToken(storedToken, envToken) {
  const stored = typeof storedToken === "string" ? storedToken.trim() : "";
  if (stored) return stored;
  const env = typeof envToken === "string" ? envToken.trim() : "";
  if (env) return env;
  return "";
}

/**
 * @param {string | null | undefined} token
 * @returns {boolean}
 */
export function shouldAttachAuthorization(token) {
  return Boolean(typeof token === "string" && token.trim());
}
