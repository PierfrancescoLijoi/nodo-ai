'use client';

import {
  ChatCircleText,
  ClipboardText,
  GearSix,
  House,
  Microphone,
  Notebook,
  TreeStructure,
  type Icon,
} from '@phosphor-icons/react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { useReviews } from '@/lib/queries';
import type { Connection } from '@/lib/runtime/connection';
import styles from './shell.module.css';

interface Destination {
  href: string;
  label: 'tabs.home' | 'tabs.meetings' | 'tabs.ask' | 'tabs.graph' | 'review.title' | 'common.settings';
  icon: Icon;
}

const PRIMARY: Destination[] = [
  { href: '/', label: 'tabs.home', icon: House },
  { href: '/meetings/', label: 'tabs.meetings', icon: Notebook },
  { href: '/ask/', label: 'tabs.ask', icon: ChatCircleText },
  { href: '/graph/', label: 'tabs.graph', icon: TreeStructure },
];

const SECONDARY: Destination[] = [
  { href: '/review/', label: 'review.title', icon: ClipboardText },
  { href: '/settings/', label: 'common.settings', icon: GearSix },
];

const normalize = (path: string) => (path.endsWith('/') ? path : `${path}/`);
const isCurrent = (pathname: string, href: string) =>
  href === '/' ? pathname === '/' : normalize(pathname).startsWith(href);

function NavLink({ item, badge }: { item: Destination; badge?: number }) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const Glyph = item.icon;
  return (
    <Link
      href={item.href}
      className={styles.link}
      aria-current={isCurrent(pathname, item.href) ? 'page' : undefined}
    >
      <Glyph size={22} aria-hidden />
      {t(item.label)}
      {badge ? (
        <span className={styles.badge} aria-label={t('review.pending', { count: badge })}>
          {badge}
        </span>
      ) : null}
    </Link>
  );
}

export function AppShell({ connection, children }: { connection: Connection; children: ReactNode }) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const main = useRef<HTMLElement>(null);
  const reviews = useReviews();
  const waiting = reviews.data?.length ?? 0;

  // A route change moves focus to the page, so keyboard and screen reader users start at its top. The
  // first load is left alone: focus stays at the start of the document, where the skip link is.
  const shown = useRef(pathname);
  useEffect(() => {
    if (shown.current === pathname) return;
    shown.current = pathname;
    main.current?.focus({ preventScroll: true });
  }, [pathname]);

  const source = t(connection.mode === 'azure' ? 'settings.dataSourceCloud' : 'settings.dataSourceLocal');

  return (
    <>
      <a className="skip-link" href="#main">
        {t('web.skipToContent')}
      </a>
      <div className={styles.frame}>
        <aside className={styles.sidebar}>
          <Link href="/" className={styles.brand} aria-label={t('app.name')}>
            <span className={styles.mark} aria-hidden>
              <Microphone size={18} weight="fill" />
            </span>
            {t('app.name')}
          </Link>
          <nav className={styles.nav} aria-label={t('web.navLabel')}>
            {PRIMARY.map((item) => (
              <NavLink key={item.href} item={item} />
            ))}
          </nav>
          <nav className={`${styles.nav} ${styles.navSecondary}`} aria-label={t('common.settings')}>
            {SECONDARY.map((item) => (
              <NavLink key={item.href} item={item} badge={item.href === '/review/' ? waiting : undefined} />
            ))}
          </nav>
          <span className={styles.source}>{source}</span>
        </aside>
        <main id="main" ref={main} tabIndex={-1} className={styles.main}>
          {children}
        </main>
        <nav className={styles.tabbar} aria-label={t('web.navLabel')}>
          {[...PRIMARY, SECONDARY[1]!].map((item) => {
            const Glyph = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={styles.tab}
                aria-current={isCurrent(pathname, item.href) ? 'page' : undefined}
              >
                <Glyph size={24} aria-hidden />
                {t(item.label)}
              </Link>
            );
          })}
        </nav>
      </div>
    </>
  );
}
