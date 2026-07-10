import React from 'react';

/** Blue toggle switch with optional title + description on the right. */
export function Switch({ checked, onChange, label, description, style }) {
  const knob = React.createElement('button', {
    role: 'switch', 'aria-checked': !!checked,
    onClick: () => onChange && onChange(!checked),
    style: {
      width: 40, height: 22, flex: 'none', borderRadius: 999, border: 'none', padding: 2,
      background: checked ? 'var(--blue-600)' : 'var(--slate-300)', cursor: 'pointer',
      display: 'flex', justifyContent: checked ? 'flex-end' : 'flex-start',
      transition: 'background var(--duration-normal) var(--ease-out)', boxSizing: 'border-box',
    },
  }, React.createElement('span', { style: { width: 18, height: 18, borderRadius: 999, background: '#fff', boxShadow: '0 1px 2px rgba(0,0,0,.2)' } }));
  if (!label) return React.createElement('span', { style }, knob);
  return React.createElement('div', { style: { display: 'flex', gap: 10, alignItems: 'flex-start', fontFamily: 'var(--font-ui)', ...style } },
    knob,
    React.createElement('div', null,
      React.createElement('div', { style: { fontSize: 14, fontWeight: 600, color: 'var(--slate-700)', lineHeight: '22px' } }, label),
      description && React.createElement('div', { style: { fontSize: 12.5, color: 'var(--text-muted)', marginTop: 1 } }, description)
    )
  );
}
