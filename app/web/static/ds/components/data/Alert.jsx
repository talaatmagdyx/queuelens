import React from 'react';
import { Icon } from '../icons/Icon.jsx';

const TONES = {
  info:    { bg: 'var(--blue-50)',   border: 'var(--blue-200)',   icon: 'info',          iconColor: 'var(--blue-600)',  title: 'var(--blue-700)' },
  success: { bg: 'var(--green-50)',  border: 'var(--green-200)',  icon: 'shield-check',  iconColor: 'var(--green-600)', title: 'var(--green-700)' },
  warning: { bg: 'var(--amber-50)',  border: 'var(--amber-200)',  icon: 'alert-triangle',iconColor: 'var(--amber-600)', title: 'var(--amber-700)' },
  danger:  { bg: 'var(--red-50)',    border: 'var(--red-200)',    icon: 'alert-circle',  iconColor: 'var(--red-600)',   title: 'var(--red-700)' },
};

/** Tinted banner/callout: info strips, safety notes, warnings, safe-mode footer. */
export function Alert({ tone = 'info', icon, title, children, action, style }) {
  const t = TONES[tone] || TONES.info;
  return React.createElement('div', {
    style: {
      display: 'flex', alignItems: children && title ? 'flex-start' : 'center', gap: 12,
      padding: '12px 16px', background: t.bg, border: `1px solid ${t.border}`,
      borderRadius: 'var(--radius-lg)', fontFamily: 'var(--font-ui)', ...style,
    },
  },
    React.createElement(Icon, { name: icon || t.icon, size: 18, color: t.iconColor, style: { marginTop: children && title ? 2 : 0 } }),
    React.createElement('div', { style: { flex: 1, minWidth: 0 } },
      title && React.createElement('div', { style: { fontSize: 13.5, fontWeight: 600, color: t.title } }, title),
      children && React.createElement('div', { style: { fontSize: 13, color: 'var(--slate-600)', marginTop: title ? 2 : 0 } }, children)),
    action
  );
}
