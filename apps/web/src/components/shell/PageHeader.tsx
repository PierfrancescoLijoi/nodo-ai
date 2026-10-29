'use client';

import { ArrowLeft } from '@phosphor-icons/react';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import styles from './shell.module.css';

interface PageHeaderProps {
  title: string;
  intro?: string;
  backHref?: string;
  actions?: ReactNode;
}

export function PageHeader({ title, intro, backHref, actions }: PageHeaderProps) {
  const { t } = useTranslation();
  return (
    <>
      {backHref ? (
        <Link href={backHref} className={`${styles.link} ${styles.back}`}>
          <ArrowLeft size={20} aria-hidden />
          {t('common.back')}
        </Link>
      ) : null}
      <header className={styles.pageHeader}>
        <div>
          <h1>{title}</h1>
          {intro ? <p className={styles.intro}>{intro}</p> : null}
        </div>
        {actions}
      </header>
    </>
  );
}
