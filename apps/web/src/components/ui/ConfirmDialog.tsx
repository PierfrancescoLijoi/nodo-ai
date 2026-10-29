'use client';

import { useEffect, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from './primitives';
import styles from './ui.module.css';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  confirmLabel: string;
  busy?: boolean;
  onConfirm(): void;
  onCancel(): void;
  children: ReactNode;
}

/** The platform's modal dialog: focus is trapped, Escape cancels, and the page behind is inert. */
export function ConfirmDialog({
  open,
  title,
  confirmLabel,
  busy,
  onConfirm,
  onCancel,
  children,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={styles.dialog}
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
    >
      <div className={styles.stack}>
        <h2>{title}</h2>
        {children}
        <div className={styles.row}>
          <Button variant="danger" disabled={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={onCancel}>
            {t('common.cancel')}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
