import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { renderWithI18n } from '@/test/render';
import { Button, Checkbox, ErrorNotice, Progress, Segmented, TextField } from './primitives';

describe('Button', () => {
  it('is a real button that does not submit forms by default', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    renderWithI18n(<Button onClick={onClick}>Save</Button>);
    const button = screen.getByRole('button', { name: 'Save' });
    expect(button).toHaveAttribute('type', 'button');
    await user.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('does nothing while disabled', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    renderWithI18n(
      <Button disabled onClick={onClick}>
        Save
      </Button>,
    );
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('TextField', () => {
  it('ties the label, the hint and the error to the input', () => {
    renderWithI18n(
      <TextField label="Name" value="" onChange={() => undefined} hint="Up to five words" error="Required" />,
    );
    const input = screen.getByLabelText('Name');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleDescription('Required Up to five words');
    expect(screen.getByRole('alert')).toHaveTextContent('Required');
  });

  it('reports what is typed', async () => {
    const user = userEvent.setup();
    function Field() {
      const [value, setValue] = useState('');
      return <TextField label="Search" value={value} onChange={setValue} />;
    }
    renderWithI18n(<Field />);
    await user.type(screen.getByLabelText('Search'), 'queue');
    expect(screen.getByLabelText('Search')).toHaveValue('queue');
  });
});

describe('Segmented', () => {
  function Control() {
    const [value, setValue] = useState<'a' | 'b' | 'c'>('a');
    return (
      <Segmented<'a' | 'b' | 'c'>
        label="View"
        value={value}
        onChange={setValue}
        options={[
          { value: 'a', label: 'One' },
          { value: 'b', label: 'Two' },
          { value: 'c', label: 'Three' },
        ]}
      />
    );
  }

  it('is a radio group with one tab stop', () => {
    renderWithI18n(<Control />);
    expect(screen.getByRole('radiogroup', { name: 'View' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'One' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Two' })).toHaveAttribute('tabindex', '-1');
  });

  it('moves with the arrow keys and wraps around', async () => {
    const user = userEvent.setup();
    renderWithI18n(<Control />);
    screen.getByRole('radio', { name: 'One' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('radio', { name: 'Two' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Two' })).toHaveFocus();
    await user.keyboard('{ArrowLeft}{ArrowLeft}');
    expect(screen.getByRole('radio', { name: 'Three' })).toBeChecked();
  });

  it('selects on click', async () => {
    const user = userEvent.setup();
    renderWithI18n(<Control />);
    await user.click(screen.getByRole('radio', { name: 'Three' }));
    expect(screen.getByRole('radio', { name: 'Three' })).toBeChecked();
  });
});

describe('Checkbox', () => {
  it('toggles and shows its note', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithI18n(<Checkbox checked={false} onChange={onChange} label="I agree" note="Recorded" />);
    await user.click(screen.getByRole('checkbox', { name: /I agree/ }));
    expect(onChange).toHaveBeenCalledWith(true);
    expect(screen.getByText('Recorded')).toBeInTheDocument();
  });
});

describe('Progress', () => {
  it('exposes the percentage to assistive technology', () => {
    renderWithI18n(<Progress value={0.42} label="Upload" />);
    expect(screen.getByRole('progressbar', { name: 'Upload' })).toHaveAttribute('value', '42');
  });
});

describe('ErrorNotice', () => {
  it('says what happened in the chosen language and offers a retry', async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    renderWithI18n(<ErrorNotice onRetry={onRetry} />, 'it');
    expect(screen.getByRole('alert')).toHaveTextContent('Impossibile caricare');
    await user.click(screen.getByRole('button', { name: 'Riprova' }));
    expect(onRetry).toHaveBeenCalled();
  });
});
