import React from 'react';
import { Icon } from '../icons/Icon.jsx';
import { StatusPill } from '../indicators/StatusPill.jsx';

const TONES = { info: 'var(--blue-600)', success: 'var(--green-600)', park: 'var(--purple-600)' };
const TILES = { info: 'var(--blue-50)', success: 'var(--green-100)', park: 'var(--purple-100)' };

/** Radio card for wizard action selection (Replay Move / Replay Copy / Park). */
export function ActionChoiceCard({ icon, tone = 'info', title, description, tag, tagTone = 'info', selected, onSelect, style }) {
  const [hover, setHover] = React.useState(false);
  return React.createElement('div', {
    onClick: onSelect,
    onMouseEnter: () => setHover(true), onMouseLeave: () => setHover(false),
    style: {
      position: 'relative', padding: '18px 16px 16px', cursor: 'pointer', fontFamily: 'var(--font-ui)',
      background: selected ? 'var(--blue-50)' : 'var(--surface-card)',
      border: `1.5px solid ${selected ? 'var(--blue-500)' : hover ? 'var(--slate-300)' : 'var(--border-default)'}`,
      borderRadius: 'var(--radius-lg)', transition: 'border-color var(--duration-fast), background var(--duration-fast)', ...style,
    },
  },
    React.createElement('span', {
      style: {
        position: 'absolute', top: 12, left: 12, width: 16, height: 16, borderRadius: 999, boxSizing: 'border-box',
        border: selected ? '5px solid var(--blue-600)' : '1.5px solid var(--slate-300)', background: 'var(--surface-control)',
      },
    }),
    React.createElement('div', { style: { display: 'flex', gap: 12, paddingLeft: 24 } },
      React.createElement('div', { style: { width: 38, height: 38, flex: 'none', borderRadius: 10, background: TILES[tone], display: 'flex', alignItems: 'center', justifyContent: 'center' } },
        React.createElement(Icon, { name: icon, size: 18, color: TONES[tone] })),
      React.createElement('div', null,
        React.createElement('div', { style: { fontSize: 14.5, fontWeight: 600, color: 'var(--slate-900)' } }, title),
        React.createElement('div', { style: { fontSize: 12.5, color: 'var(--slate-500)', marginTop: 3, lineHeight: 1.45 } }, description),
        tag && React.createElement(StatusPill, { tone: tagTone, size: 'sm', style: { marginTop: 10 } }, tag)))
  );
}
