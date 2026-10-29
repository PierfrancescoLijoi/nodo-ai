import { z } from 'zod';

/** HTTPS everywhere; plain HTTP only for the developer's own machine. */
export function isAllowedUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    return url.protocol === 'https:' || (url.protocol === 'http:' && loopback);
  } catch {
    return false;
  }
}

const allowedUrl = z.string().refine(isAllowedUrl, 'must be an https address (http only on localhost)');

/** What the Azure web container publishes at /config.json (rendered from its environment at start). */
export const cloudConfigSchema = z.object({
  mode: z.literal('cloud'),
  // Empty means the API is on the page's own origin (Front Door routes /v1 to the gateway).
  apiBaseUrl: z.union([z.literal(''), allowedUrl]).default(''),
  oidc: z.object({
    issuer: allowedUrl,
    clientId: z.string().min(1),
    scope: z.string().min(1),
  }),
});

export type CloudConfig = z.infer<typeof cloudConfigSchema>;

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ConfigError';
  }
}

/**
 * The deployment's configuration, or `undefined` when there is none (the local engine serves the same
 * files without a /config.json). A file that exists but is wrong is an error: a half-configured
 * deployment must say so, not fall back to a different mode.
 */
export async function loadCloudConfig(
  fetchImpl: typeof fetch = (...args) => fetch(...args),
): Promise<CloudConfig | undefined> {
  let response: Response;
  try {
    response = await fetchImpl('/config.json', {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
  } catch {
    return undefined;
  }
  if (!response.ok || !(response.headers.get('content-type') ?? '').includes('json')) return undefined;
  const parsed = cloudConfigSchema.safeParse(await response.json().catch(() => undefined));
  if (!parsed.success) throw new ConfigError('config.json does not describe a valid deployment');
  return parsed.data;
}
