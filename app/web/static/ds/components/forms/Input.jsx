import React from 'react';

const baseControl = (focus) => ({
  width: '100%', boxSizing: 'border-box', height: 38, padding: '0 12px',
  fontFamily: 'var(--font-ui)', fontSize: 14, color: 'var(--slate-700)',
  background: 'var(--surface-control)', border: `1px solid ${focus ? 'var(--blue-500)' : 'var(--border-default)'}`,
  borderRadius: 'var(--radius-md)', outline: 'none',
  boxShadow: focus ? '0 0 0 3px var(--blue-100)' : 'none',
  transition: 'border-color var(--duration-fast), box-shadow var(--duration-fast)',
});

export function FieldLabel({ children, required }) {
  return React.createElement('label', {
    style: { display: 'block', fontFamily: 'var(--font-ui)', fontSize: 13, fontWeight: 600, color: 'var(--slate-700)', marginBottom: 6 },
  }, children, required && React.createElement('span', { style: { color: 'var(--red-600)', marginLeft: 3 } }, '*'));
}

/** Text input with optional label, required asterisk, unit suffix, valid check. */
export function Input({ label, required, value, defaultValue, onChange, placeholder, type = 'text', suffix, valid, readOnly, style }) {
  const [focus, setFocus] = React.useState(false);
  return React.createElement('div', { style: { fontFamily: 'var(--font-ui)', minWidth: 0, ...style } },
    label && React.createElement(FieldLabel, { required }, label),
    React.createElement('div', { style: { position: 'relative' } },
      React.createElement('input', {
        type, value, defaultValue, placeholder, readOnly,
        onChange: (e) => onChange && onChange(e.target.value),
        onFocus: () => setFocus(true), onBlur: () => setFocus(false),
        style: {
          ...baseControl(focus),
          paddingRight: suffix || valid ? 44 : 12,
          background: readOnly ? 'var(--slate-50)' : 'var(--surface-control)',
        },
      }),
      suffix && React.createElement('span', {
        style: { position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', fontSize: 12, color: 'var(--slate-400)' },
      }, suffix),
      valid && React.createElement('span', {
        style: { position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--green-600)', fontSize: 14, fontWeight: 700 },
      }, '✓')
    )
  );
}

export { baseControl as _baseControl };
