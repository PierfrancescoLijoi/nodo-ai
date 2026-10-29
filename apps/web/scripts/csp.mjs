// The content security policy of the web app, in one place for both hosts (local engine, Azure container).
// Scripts: this origin plus the exact inline scripts the build produced (by hash). Nothing else may run,
// there is no eval, and styles come from files only.
import { createHash } from 'node:crypto';

export const CONNECT_PLACEHOLDER = '__CONNECT_SRC__';

export const sha256 = (text) => `'sha256-${createHash('sha256').update(text, 'utf8').digest('base64')}'`;

// Inline script bodies of one HTML document (external scripts have a src and are covered by 'self').
export function inlineScripts(html) {
  return [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)]
    .filter(([, attributes]) => !/\bsrc\s*=/.test(attributes))
    .map(([, , body]) => body)
    .filter((body) => body.trim() !== '');
}

// Anything the policy would silently block must fail the build instead of failing in a user's browser.
export function violations(html) {
  const found = [];
  if (/<style\b/i.test(html)) found.push('inline <style>');
  if (/\sstyle\s*=\s*["']/i.test(html)) found.push('inline style attribute');
  if (/\son[a-z]+\s*=\s*["']/i.test(html)) found.push('inline event handler');
  if (/<script\b[^>]*\btype\s*=\s*["']module["'][^>]*>\s*[^<\s]/i.test(html))
    found.push('inline module script');
  return found;
}

export function policy({ scriptHashes, connect }) {
  return [
    "default-src 'none'",
    `script-src 'self' ${scriptHashes.join(' ')}`.trim(),
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src ${connect}`.trim(),
    "media-src 'self' blob:",
    "manifest-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
  ].join('; ');
}

// Local engine: the sign-in for "Copy to Azure" talks to an identity provider the user names, so https is
// open for connections (and this machine's own http, for a developer's provider); script execution, which
// is what could abuse that, stays locked to the hashes above.
export const localPolicy = (scriptHashes) =>
  policy({ scriptHashes, connect: "'self' https: http://127.0.0.1:* http://localhost:*" });

// Azure container: the image is built once, so the origins that depend on the deployment (identity
// provider, audio storage) are filled in when the container starts.
export const cloudPolicy = (scriptHashes) =>
  policy({ scriptHashes, connect: `'self' ${CONNECT_PLACEHOLDER}` });
