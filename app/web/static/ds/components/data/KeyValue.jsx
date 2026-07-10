import React from 'react';
import { Icon } from '../icons/Icon.jsx';

/** Stacked label/value pairs for detail panels (Message Details, Action Details). */
export function KeyValue({ items = [], gap = 14, style }) {
  return React.createElement('div', { style: { display: 'flex', flexDirection: 'column', gap, fontFamily: 'var(--font-ui)', ...style } },
    items.map((it, i) => React.createElement('div', { key: i },
      React.createElement('div', { style: { fontSize: 12, color: 'var(--text-muted)', marginBottom: 3 } }, it.label),
      React.createElement('div', {
        style: {
          fontSize: 13.5, color: 'var(--slate-900)', fontWeight: 500,
          fontFamily: it.mono ? 'var(--font-mono)' : 'var(--font-ui)',
          display: 'flex', alignItems: 'center', gap: 6, wordBreak: 'break-all',
        },
      },
        it.value,
        it.copy && React.createElement(Icon, { name: 'copy', size: 13, color: 'var(--slate-400)', style: { cursor: 'pointer', flex: 'none' } }))))
  );
}
