import React from 'react';
import { Icon } from '../icons/Icon.jsx';

/**
 * Tabs. variant="underline": blue underline + count pills (Queues page, payload tabs).
 * variant="boxed": icon tabs with blue underline on active (Configuration page).
 */
export function Tabs({ tabs = [], active, onChange, variant = 'underline', style }) {
  return React.createElement('div', {
    style: { display: 'flex', gap: variant === 'underline' ? 28 : 24, borderBottom: '1px solid var(--border-default)', fontFamily: 'var(--font-ui)', overflowX: 'auto', ...style },
  }, tabs.map((t) => {
    const tab = typeof t === 'string' ? { id: t, label: t } : t;
    const isActive = tab.id === active;
    return React.createElement('button', {
      key: tab.id,
      onClick: () => onChange && onChange(tab.id),
      style: {
        display: 'inline-flex', alignItems: 'center', gap: 8, padding: '0 2px 12px',
        background: 'none', border: 'none', cursor: 'pointer', flex: 'none', whiteSpace: 'nowrap',
        fontFamily: 'var(--font-ui)', fontSize: 14, fontWeight: 600,
        color: isActive ? 'var(--blue-600)' : 'var(--slate-500)',
        boxShadow: isActive ? 'inset 0 -2px 0 var(--blue-600)' : 'none',
        marginBottom: -1,
      },
    },
      tab.icon && React.createElement(Icon, { name: tab.icon, size: 16 }),
      tab.label,
      tab.count != null && React.createElement('span', {
        style: {
          minWidth: 20, height: 20, padding: '0 6px', borderRadius: 999,
          background: isActive ? 'var(--blue-100)' : 'var(--slate-100)',
          color: isActive ? 'var(--blue-700)' : 'var(--slate-500)',
          fontSize: 12, fontWeight: 600, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        },
      }, tab.count)
    );
  }));
}
