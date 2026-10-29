import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'scripts/**/*.test.mjs'],
    // Logic and primitives are measured here. The screens are exercised end to end in a real browser
    // (e2e/), where their behaviour is asserted, so they are not part of this number.
    coverage: {
      provider: 'v8',
      include: [
        'src/lib/**',
        'src/components/ui/**',
        'src/features/**/*.ts',
        'src/providers/**',
        'scripts/*.mjs',
      ],
      exclude: [
        '**/*.test.*',
        'src/lib/i18n/{da,en,it,pl}.ts',
        'src/lib/queries.ts',
        'src/providers/{auth,connection,providers}.tsx',
        'scripts/seal-csp.mjs',
      ],
      thresholds: { lines: 80, functions: 80, statements: 80 },
    },
  },
});
