import React from 'react';
import { Icon } from '../icons/Icon.jsx';

/** Search input with leading magnifier icon. */
export function SearchInput({ value, onChange, placeholder = 'Search…', style }) {
  const [focus, setFocus] = React.useState(false);
  return React.createElement('div', { style: { position: 'relative', fontFamily: 'var(--font-ui)', minWidth: 0, ...style } },
    React.createElement('span', { style: { position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' } },
      React.createElement(Icon, { name: 'search', size: 16, color: 'var(--slate-400)' })),
    React.createElement('input', {
      value, placeholder,
      onChange: (e) => onChange && onChange(e.target.value),
      onFocus: () => setFocus(true), onBlur: () => setFocus(false),
      style: {
        width: '100%', boxSizing: 'border-box', height: 38, padding: '0 12px 0 36px',
        fontFamily: 'var(--font-ui)', fontSize: 14, color: 'var(--slate-700)',
        background: 'var(--surface-control)', border: `1px solid ${focus ? 'var(--blue-500)' : 'var(--border-default)'}`,
        borderRadius: 'var(--radius-md)', outline: 'none',
        boxShadow: focus ? '0 0 0 3px var(--blue-100)' : 'none',
      },
    })
  );
}
