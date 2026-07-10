import React from 'react';
import { Icon } from '../icons/Icon.jsx';

/** Wizard step indicator: numbered circles joined by arrows; done=green check, active=blue. */
export function Stepper({ steps = [], current = 0, style }) {
  return React.createElement('div', {
    style: {
      display: 'flex', alignItems: 'center', gap: 12, padding: '14px 18px',
      border: '1px solid var(--border-default)', borderRadius: 'var(--radius-lg)',
      fontFamily: 'var(--font-ui)', background: 'var(--surface-card)', overflowX: 'auto', ...style,
    },
  }, steps.flatMap((label, i) => {
    const state = i < current ? 'done' : i === current ? 'active' : 'todo';
    const circle = React.createElement('span', {
      style: {
        width: 26, height: 26, flex: 'none', borderRadius: 999,
        background: state === 'done' ? 'var(--green-600)' : state === 'active' ? 'var(--blue-600)' : 'var(--surface-control)',
        border: state === 'todo' ? '1.5px solid var(--slate-300)' : 'none',
        color: state === 'todo' ? 'var(--slate-500)' : '#fff',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 13, fontWeight: 600, boxSizing: 'border-box',
      },
    }, state === 'done' ? React.createElement(Icon, { name: 'check', size: 14, color: '#fff', strokeWidth: 3 }) : i + 1);
    const item = React.createElement('span', {
      key: 's' + i,
      style: { display: 'inline-flex', alignItems: 'center', gap: 10, whiteSpace: 'nowrap' },
    }, circle, React.createElement('span', {
      style: { fontSize: 14, fontWeight: 600, color: state === 'active' ? 'var(--blue-600)' : state === 'done' ? 'var(--slate-700)' : 'var(--slate-400)' },
    }, label));
    if (i === steps.length - 1) return [item];
    return [item, React.createElement(Icon, { key: 'a' + i, name: 'arrow-right', size: 15, color: 'var(--slate-300)', style: { flex: 'none' } })];
  }));
}
