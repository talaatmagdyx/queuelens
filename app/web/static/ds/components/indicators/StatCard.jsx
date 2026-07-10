import React from 'react';
import { Icon } from '../icons/Icon.jsx';

const TONES = {
  info:    { tile: 'var(--blue-50)',    icon: 'var(--blue-600)' },
  park:    { tile: 'var(--purple-100)', icon: 'var(--purple-600)' },
  warning: { tile: 'var(--amber-100)',  icon: 'var(--amber-600)' },
  danger:  { tile: 'var(--red-100)',    icon: 'var(--red-600)' },
  success: { tile: 'var(--green-100)',  icon: 'var(--green-600)' },
};

/** Dashboard stat card: pastel icon tile + big number + label lines + optional link. */
export function StatCard({ icon, tone = 'info', value, label, sublabel, link, onLinkClick, style }) {
  const t = TONES[tone] || TONES.info;
  return React.createElement('div', {
    style: {
      display: 'flex', gap: 14, alignItems: 'flex-start', padding: 20,
      background: 'var(--surface-card)', border: '1px solid var(--border-default)',
      borderRadius: 'var(--radius-xl)', fontFamily: 'var(--font-ui)', minWidth: 0, ...style,
    },
  },
    React.createElement('div', {
      style: { width: 44, height: 44, flex: 'none', borderRadius: 'var(--radius-xl)', background: t.tile, display: 'flex', alignItems: 'center', justifyContent: 'center' },
    }, React.createElement(Icon, { name: icon, size: 22, color: t.icon })),
    React.createElement('div', { style: { minWidth: 0, flex: 1 } },
      React.createElement('div', { style: { fontSize: 24, fontWeight: 700, color: 'var(--text-heading)', lineHeight: 1.2 } }, value),
      React.createElement('div', { style: { fontSize: 13, color: 'var(--text-body)', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' } }, label),
      sublabel && React.createElement('div', { style: { fontSize: 13, color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' } }, sublabel),
      link && React.createElement('a', {
        href: '#', onClick: (e) => { e.preventDefault(); onLinkClick && onLinkClick(); },
        style: { display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 13, fontWeight: 600, color: 'var(--text-link)', textDecoration: 'none', marginTop: 6 },
      }, link, React.createElement(Icon, { name: 'arrow-right', size: 14 }))
    )
  );
}
