import React from 'react';
import { Icon } from '../icons/Icon.jsx';

/** 16px checkbox — blue filled when checked. */
export function Checkbox({ checked, onChange, label, style }) {
  const box = React.createElement('button', {
    role: 'checkbox', 'aria-checked': !!checked,
    onClick: () => onChange && onChange(!checked),
    style: {
      width: 16, height: 16, flex: 'none', borderRadius: 4, padding: 0, cursor: 'pointer',
      background: checked ? 'var(--blue-600)' : 'var(--surface-control)',
      border: `1px solid ${checked ? 'var(--blue-600)' : 'var(--border-strong)'}`,
      display: 'flex', alignItems: 'center', justifyContent: 'center', boxSizing: 'border-box',
    },
  }, checked && React.createElement(Icon, { name: 'check', size: 12, color: '#fff', strokeWidth: 3 }));
  if (!label) return React.createElement('span', { style }, box);
  return React.createElement('label', { style: { display: 'inline-flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-ui)', fontSize: 13, color: 'var(--slate-600)', cursor: 'pointer', ...style } }, box, label);
}
