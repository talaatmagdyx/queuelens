import React from 'react';
import { Icon } from '../icons/Icon.jsx';
import { Sparkline } from '../indicators/Sparkline.jsx';

function NavItem({ item, active, onClick }) {
  const [hover, setHover] = React.useState(false);
  return React.createElement('button', {
    onClick,
    onMouseEnter: () => setHover(true), onMouseLeave: () => setHover(false),
    style: {
      display: 'flex', alignItems: 'center', gap: 12, width: '100%', height: 40,
      padding: '0 12px', border: 'none', borderRadius: 'var(--radius-md)', cursor: 'pointer',
      background: active ? 'var(--blue-50)' : hover ? 'var(--slate-100)' : 'transparent',
      color: active ? 'var(--blue-600)' : 'var(--slate-600)',
      fontFamily: 'var(--font-ui)', fontSize: 14, fontWeight: 600, textAlign: 'left',
      transition: 'background var(--duration-fast)', boxSizing: 'border-box',
    },
  }, React.createElement(Icon, { name: item.icon, size: 18 }), item.label);
}

/**
 * QueueLens left sidebar: logo, grouped nav, Broker Live card, Safety First card, user footer.
 * groups: [{label:'MAIN', items:[{id,label,icon}]}]
 */
export function SidebarNav({ groups = [], activeId, onNavigate, broker, safety, user, style }) {
  return React.createElement('aside', {
    style: {
      width: 'var(--sidebar-width)', flex: 'none', boxSizing: 'border-box',
      position: 'sticky', top: 0, height: '100vh', overflowY: 'auto',
      background: 'var(--surface-sidebar)', borderRight: '1px solid var(--border-default)',
      display: 'flex', flexDirection: 'column', padding: '16px 12px', fontFamily: 'var(--font-ui)', ...style,
    },
  },
    // Logo
    React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 10, padding: '4px 8px 18px' } },
      React.createElement('div', { style: { width: 36, height: 36, borderRadius: 10, background: 'var(--blue-600)', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' } },
        React.createElement(Icon, { name: 'scan-eye', size: 20, color: '#fff' })),
      React.createElement('div', { style: { fontSize: 19, fontWeight: 700 } },
        React.createElement('span', { style: { color: 'var(--slate-900)' } }, 'Queue'),
        React.createElement('span', { style: { color: 'var(--blue-600)' } }, 'Lens'))),
    // Groups
    groups.map((g) => React.createElement('div', { key: g.label, style: { marginBottom: 14 } },
      React.createElement('div', { style: { fontSize: 11, fontWeight: 600, letterSpacing: 'var(--tracking-caps)', color: 'var(--text-muted)', textTransform: 'uppercase', padding: '8px 12px' } }, g.label),
      React.createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: 2 } },
        g.items.map((it) => React.createElement(NavItem, { key: it.id, item: it, active: it.id === activeId, onClick: () => onNavigate && onNavigate(it.id) }))))),
    React.createElement('div', { style: { flex: 1 } }),
    // Broker card
    broker && (broker.offline
      ? React.createElement('div', {
          style: { background: 'var(--red-50)', border: '1px solid var(--red-200)', borderRadius: 'var(--radius-lg)', padding: '12px 14px', marginBottom: 12 },
        },
          React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600, color: 'var(--red-700)' } },
            React.createElement('span', { style: { width: 8, height: 8, borderRadius: 999, background: 'var(--red-600)' } }), 'Broker Offline'),
          React.createElement('div', { style: { fontSize: 12, color: 'var(--slate-500)', lineHeight: 1.8, marginTop: 6 } },
            React.createElement('div', null, broker.host),
            React.createElement('div', null, 'vhost: ', broker.vhost),
            React.createElement('div', { style: { color: 'var(--red-600)', fontWeight: 600 } }, 'no response')))
      : React.createElement('div', {
          style: { background: 'var(--green-50)', border: '1px solid var(--green-200)', borderRadius: 'var(--radius-lg)', padding: '12px 14px', marginBottom: 12 },
        },
          React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600, color: 'var(--green-700)' } },
            React.createElement('span', { style: { width: 8, height: 8, borderRadius: 999, background: 'var(--green-600)' } }), 'Broker Live'),
          React.createElement('div', { style: { display: 'flex', alignItems: 'flex-end', gap: 8 } },
            React.createElement('div', { style: { flex: 1, fontSize: 12, color: 'var(--slate-500)', lineHeight: 1.8, marginTop: 6 } },
              React.createElement('div', null, broker.host),
              React.createElement('div', null, 'vhost: ', broker.vhost),
              React.createElement('div', null, 'latency: ', broker.latency)),
            React.createElement(Sparkline, { points: [5, 9, 6, 15, 7, 10, 6], width: 56, height: 26 })))),
    // Safety card
    safety && React.createElement('div', {
      style: { background: 'var(--blue-50)', border: '1px solid var(--blue-200)', borderRadius: 'var(--radius-lg)', padding: '12px 14px', marginBottom: 12 },
    },
      React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600, color: 'var(--blue-600)' } },
        React.createElement(Icon, { name: 'shield-check', size: 16 }), 'Safety First'),
      React.createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 } },
        safety.map((s) => React.createElement('div', { key: s, style: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--slate-600)' } },
          React.createElement(Icon, { name: 'check', size: 13, color: 'var(--green-600)', strokeWidth: 2.5 }), s)))),
    // User footer
    user && React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 10, padding: '10px 8px 2px', borderTop: '1px solid var(--border-default)' } },
      React.createElement('div', { style: { width: 34, height: 34, borderRadius: 999, background: 'var(--slate-200)', color: 'var(--slate-600)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, flex: 'none' } }, user.initials),
      React.createElement('div', { style: { flex: 1, minWidth: 0 } },
        React.createElement('div', { style: { fontSize: 13.5, fontWeight: 600, color: 'var(--slate-900)' } }, user.name),
        React.createElement('div', { style: { fontSize: 12, color: 'var(--slate-500)' } }, user.role)),
      React.createElement(Icon, { name: 'chevron-down', size: 16, color: 'var(--slate-400)' }))
  );
}
