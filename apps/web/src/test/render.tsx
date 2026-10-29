import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { I18nextProvider } from 'react-i18next';

import { createI18n, type Locale } from '@/lib/i18n';

/** Renders with the app's translations, in English unless told otherwise. */
export function renderWithI18n(ui: ReactElement, locale: Locale = 'en'): RenderResult {
  return render(<I18nextProvider i18n={createI18n(locale)}>{ui}</I18nextProvider>);
}
