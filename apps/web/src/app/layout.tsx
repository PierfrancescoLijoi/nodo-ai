import type { Metadata, Viewport } from 'next';
import { GeistMono } from 'geist/font/mono';
import { GeistSans } from 'geist/font/sans';
import type { ReactNode } from 'react';

import { Providers } from '@/providers/providers';

import './globals.css';

export const metadata: Metadata = {
  title: { default: 'NODO', template: '%s · NODO' },
  description: 'Your meeting memory, with the sentence behind every answer.',
  robots: { index: false, follow: false },
  referrer: 'no-referrer',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  colorScheme: 'light dark',
};

// Runs before the first paint so a stored dark or light choice does not flash the other one. It is a
// constant, and the build hashes it into the content policy (scripts/seal-csp.mjs).
const THEME_BOOT = `try{var p=JSON.parse(localStorage.getItem('nodo.prefs')||'{}');if(p.theme==='light'||p.theme==='dark')document.documentElement.dataset.theme=p.theme}catch(e){}`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
