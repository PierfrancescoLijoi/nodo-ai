'use client';

import type { Icon } from '@phosphor-icons/react';
import Link from 'next/link';
import { useId, useRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import styles from './ui.module.css';

const join = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(' ');

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  icon?: Icon;
  fullWidth?: boolean;
}

export function Button({
  variant = 'primary',
  icon: IconComponent,
  fullWidth,
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      className={join(styles.button, styles[variant], fullWidth && styles.full, className)}
      {...rest}
    >
      {IconComponent ? <IconComponent size={20} weight="bold" aria-hidden /> : null}
      {children}
    </button>
  );
}

interface LinkButtonProps {
  href: string;
  variant?: 'primary' | 'secondary' | 'ghost';
  icon?: Icon;
  fullWidth?: boolean;
  children: ReactNode;
}

/** A navigation that looks like a button: it is a link, so it opens in a new tab and works without script. */
export function LinkButton({
  href,
  variant = 'primary',
  icon: IconComponent,
  fullWidth,
  children,
}: LinkButtonProps) {
  return (
    <Link href={href} className={join(styles.button, styles[variant], fullWidth && styles.full)}>
      {IconComponent ? <IconComponent size={20} weight="bold" aria-hidden /> : null}
      {children}
    </Link>
  );
}

interface SurfaceProps {
  tone?: 'default' | 'accent' | 'warning';
  raised?: boolean;
  as?: 'div' | 'section' | 'article' | 'li';
  children: ReactNode;
  className?: string;
  'aria-labelledby'?: string;
}

export function Surface({
  tone = 'default',
  raised,
  as: Tag = 'div',
  className,
  children,
  ...rest
}: SurfaceProps) {
  return (
    <Tag
      className={join(
        styles.surface,
        tone === 'accent' && styles.accent,
        tone === 'warning' && styles.warning,
        raised && styles.raised,
        className,
      )}
      {...rest}
    >
      {children}
    </Tag>
  );
}

export function Tag({ label, tone = 'default' }: { label: string; tone?: 'default' | 'accent' | 'warning' }) {
  return (
    <span
      className={join(
        styles.tag,
        tone === 'accent' && styles.tagAccent,
        tone === 'warning' && styles.tagWarning,
      )}
    >
      {label}
    </span>
  );
}

export function Skeleton({ size = 'block' }: { size?: 'line' | 'block' | 'card' | 'tall' }) {
  return <span className={join(styles.skeleton, styles[size])} aria-hidden />;
}

interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'onChange' | 'value'> {
  label: string;
  value: string;
  onChange(value: string): void;
  error?: string;
  hint?: string;
}

export function TextField({ label, value, onChange, error, hint, id, ...rest }: TextFieldProps) {
  const generated = useId();
  const inputId = id ?? generated;
  const describedBy = [error ? `${inputId}-error` : undefined, hint ? `${inputId}-hint` : undefined]
    .filter(Boolean)
    .join(' ');
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={inputId}>
        {label}
      </label>
      <input
        id={inputId}
        className={styles.input}
        value={value}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        onChange={(event) => onChange(event.target.value)}
        {...rest}
      />
      {hint ? (
        <span id={`${inputId}-hint`} className={styles.hint}>
          {hint}
        </span>
      ) : null}
      {error ? (
        <span id={`${inputId}-error`} className={styles.errorText} role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}

interface SegmentedProps<T extends string> {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange(value: T): void;
}

/** A radio group drawn as a segmented control; arrow keys move and select like native radios. */
export function Segmented<T extends string>({ label, value, options, onChange }: SegmentedProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (from: number, step: number) => {
    const next = (from + step + options.length) % options.length;
    const option = options[next];
    if (!option) return;
    onChange(option.value);
    refs.current[next]?.focus();
  };
  return (
    <div role="radiogroup" aria-label={label} className={styles.segmented}>
      {options.map((option, index) => {
        const checked = option.value === value;
        return (
          <button
            key={option.value}
            ref={(element) => {
              refs.current[index] = element;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked ? 0 : -1}
            className={styles.segment}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                event.preventDefault();
                move(index, 1);
              } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                event.preventDefault();
                move(index, -1);
              }
            }}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

interface CheckboxProps {
  checked: boolean;
  onChange(checked: boolean): void;
  label: string;
  note?: string;
}

export function Checkbox({ checked, onChange, label, note }: CheckboxProps) {
  return (
    <label className={styles.checkbox}>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span className={styles.stack}>
        <span>{label}</span>
        {note ? <span className={styles.hint}>{note}</span> : null}
      </span>
    </label>
  );
}

export function SectionHeader({
  title,
  actionLabel,
  onAction,
}: {
  title: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className={styles.sectionHeader}>
      <h2>{title}</h2>
      {actionLabel && onAction ? (
        <Button variant="ghost" onClick={onAction}>
          {actionLabel}
        </Button>
      ) : null}
    </div>
  );
}

export function Progress({ value, label }: { value: number; label: string }) {
  return (
    <progress className={styles.progress} value={Math.round(value * 100)} max={100} aria-label={label} />
  );
}

export function ErrorNotice({ onRetry }: { onRetry?: () => void }) {
  const { t } = useTranslation();
  return (
    <Surface tone="warning">
      <p role="alert">{t('common.loadError')}</p>
      {onRetry ? (
        <div>
          <Button variant="secondary" onClick={onRetry}>
            {t('common.retry')}
          </Button>
        </div>
      ) : null}
    </Surface>
  );
}
