import type { NextConfig } from 'next';
import { PHASE_DEVELOPMENT_SERVER } from 'next/constants';

// Production is a static export: one artifact that the local engine and the Azure web container both
// serve (ADR-027). Development runs the Next server and forwards the API to a running engine.
export default function nextConfig(phase: string): NextConfig {
  const common: NextConfig = { reactStrictMode: true, poweredByHeader: false, images: { unoptimized: true } };
  if (phase !== PHASE_DEVELOPMENT_SERVER) return { ...common, output: 'export', trailingSlash: true };
  const engine = process.env.NODO_DEV_API ?? 'http://127.0.0.1:8765';
  return {
    ...common,
    async rewrites() {
      return [
        { source: '/v1/:path*', destination: `${engine}/v1/:path*` },
        { source: '/healthz', destination: `${engine}/healthz` },
      ];
    },
  };
}
