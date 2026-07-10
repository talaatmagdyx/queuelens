import React from 'react';
import { FieldLabel } from './Input.jsx';
import { Icon } from '../icons/Icon.jsx';

/** Native select styled as a QueueLens control, chevron on the right. */
export function Select({ label, required, value, defaultValue, onChange, options = [], style }) {
  const [focus, setFocus] = React.useState(false);
  return React.createElement('div', { style: { fontFamily: 'var(--font-ui)', minWidth: 0, ...style } },
    label && React.createElement(FieldLabel, { required }, label),
    React.createElement('div', { style: { position: 'relative' } },
      React.createElement('select', {
        value, defaultValue,
        onChange: (e) => onChange && onChange(e.target.value),
        onFocus: () => setFocus(true), onBlur: () => setFocus(false),
        style: {
          width: '100%', boxSizing: 'border-box', height: 38, padding: '0 32px 0 12px',
          fontFamily: 'var(--font-ui)', fontSize: 14, color: 'var(--slate-700)',
          background: 'var(--surface-control)', border: `1px solid ${focus ? 'var(--blue-500)' : 'var(--border-default)'}`,
          borderRadius: 'var(--radius-md)', outline: 'none', appearance: 'none', cursor: 'pointer',
          boxShadow: focus ? '0 0 0 3px var(--blue-100)' : 'none',
        },
      }, options.map((o) => {
        const opt = typeof o === 'string' ? { value: o, label: o } : o;
        return React.createElement('option', { key: opt.value, value: opt.value }, opt.label);
      })),
      React.createElement('span', { style: { position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' } },
        React.createElement(Icon, { name: 'chevron-down', size: 16, color: 'var(--slate-400)' }))
    )
  );
}
