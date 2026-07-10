import React from 'react';
import { Icon } from '../icons/Icon.jsx';
import { StatusPill } from '../indicators/StatusPill.jsx';
import { Button } from '../actions/Button.jsx';
import { IconButton } from '../actions/IconButton.jsx';

const ENV_DOT = { development: 'var(--blue-600)', staging: 'var(--amber-600)', production: 'var(--red-600)' };

function Menu({ open, onClose, children, width = 240 }) {
  React.useEffect(() => {
    if (!open) return;
    const h = () => onClose();
    document.addEventListener('click', h);
    return () => document.removeEventListener('click', h);
  }, [open, onClose]);
  if (!open) return null;
  return React.createElement('div', {
    style: {
      position: 'absolute', top: 'calc(100% + 8px)', left: 0, minWidth: width, zIndex: 60,
      background: 'var(--surface-raised)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-lg)',
      boxShadow: 'var(--shadow-popover)', padding: 6, fontFamily: 'var(--font-ui)',
    },
  }, children);
}

function MenuItem({ active, onClick, dot, label, sub }) {
  const [hover, setHover] = React.useState(false);
  return React.createElement('button', {
    onClick,
    onMouseEnter: () => setHover(true), onMouseLeave: () => setHover(false),
    style: {
      display: 'flex', alignItems: 'center', gap: 10, width: '100%', textAlign: 'left',
      padding: '8px 10px', border: 'none', borderRadius: 'var(--radius-md)', cursor: 'pointer',
      background: hover ? 'var(--slate-50)' : 'transparent', fontFamily: 'var(--font-ui)',
    },
  },
    dot && React.createElement('span', { style: { width: 8, height: 8, flex: 'none', borderRadius: 999, background: dot } }),
    React.createElement('span', { style: { flex: 1, minWidth: 0 } },
      React.createElement('span', { style: { display: 'block', fontSize: 13.5, fontWeight: 600, color: 'var(--slate-900)' } }, label),
      sub && React.createElement('span', { style: { display: 'block', fontSize: 12, color: 'var(--slate-500)', marginTop: 1 } }, sub)),
    active && React.createElement(Icon, { name: 'check', size: 14, color: 'var(--blue-600)', strokeWidth: 2.5 })
  );
}

/**
 * Top bar. Static by default; pass `envs`/`onEnvChange` to turn the environment
 * pill into a switcher, and `vhosts`/`onVhostChange` to make vhost a dropdown.
 */
export function TopBar({
  env = 'DEVELOPMENT', envs, activeEnv, onEnvChange,
  vhost = '/', vhosts, onVhostChange,
  version = 'RabbitMQ 3.13.2', lastRefreshed = '5s ago', notifications = 2,
  brokerLive = true, onBrokerToggle, theme = 'light', onThemeToggle,
  userName, onRefresh, onBell, style,
}) {
  const [envOpen, setEnvOpen] = React.useState(false);
  const [vhostOpen, setVhostOpen] = React.useState(false);
  const sep = React.createElement('span', { style: { width: 1, height: 18, background: 'var(--border-default)', flex: 'none' } });
  const current = envs && envs.find((e) => e.id === activeEnv);
  const envLabel = current ? current.label : env;
  const envTone = (current ? current.id : String(env).toLowerCase()) === 'production' ? 'danger' : 'info';

  const envPill = React.createElement(StatusPill, {
    tone: envTone, dot: true, size: 'sm',
    style: { textTransform: 'uppercase', letterSpacing: '0.04em', cursor: envs ? 'pointer' : 'default' },
  }, envLabel, envs && React.createElement(Icon, { name: 'chevron-down', size: 12 }));

  return React.createElement('header', {
    style: {
      height: 'var(--topbar-height)', boxSizing: 'border-box', flex: 'none',
      display: 'flex', alignItems: 'center', gap: 14, padding: '0 24px',
      background: 'var(--surface-card)', borderBottom: '1px solid var(--border-default)', fontFamily: 'var(--font-ui)', ...style,
    },
  },
    React.createElement('span', { onClick: () => onBrokerToggle && onBrokerToggle(), title: onBrokerToggle ? 'Click to simulate broker outage' : undefined, style: { cursor: onBrokerToggle ? 'pointer' : 'default' } },
      React.createElement(StatusPill, { tone: brokerLive ? 'success' : 'danger', dot: true, size: 'sm', style: { textTransform: 'uppercase', letterSpacing: '0.04em' } }, brokerLive ? 'Broker Live' : 'Broker Offline')),
    // Environment switcher
    React.createElement('span', { style: { position: 'relative' }, onClick: (e) => { if (envs) { e.stopPropagation(); setEnvOpen(!envOpen); setVhostOpen(false); } } },
      envPill,
      envs && React.createElement(Menu, { open: envOpen, onClose: () => setEnvOpen(false), width: 260 },
        React.createElement('div', { style: { padding: '6px 10px 4px', fontSize: 11, fontWeight: 600, letterSpacing: 'var(--tracking-caps)', textTransform: 'uppercase', color: 'var(--text-muted)' } }, 'Environment'),
        envs.map((e) => React.createElement(MenuItem, {
          key: e.id, active: e.id === activeEnv, dot: ENV_DOT[e.id] || 'var(--slate-400)',
          label: e.label, sub: e.host,
          onClick: () => { setEnvOpen(false); onEnvChange && onEnvChange(e.id); },
        })))),
    sep,
    React.createElement('span', { style: { fontSize: 13, color: 'var(--slate-600)', fontWeight: 500 } }, version),
    sep,
    // VHost switcher
    React.createElement('span', {
      style: { position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 13, color: 'var(--slate-600)', cursor: vhosts ? 'pointer' : 'default' },
      onClick: (e) => { if (vhosts) { e.stopPropagation(); setVhostOpen(!vhostOpen); setEnvOpen(false); } },
    },
      'vhost: ', React.createElement('b', { style: { fontWeight: 600, color: 'var(--slate-900)' } }, vhost),
      vhosts && React.createElement(Icon, { name: 'chevron-down', size: 13, color: 'var(--slate-400)' }),
      vhosts && React.createElement(Menu, { open: vhostOpen, onClose: () => setVhostOpen(false), width: 220 },
        React.createElement('div', { style: { padding: '6px 10px 4px', fontSize: 11, fontWeight: 600, letterSpacing: 'var(--tracking-caps)', textTransform: 'uppercase', color: 'var(--text-muted)' } }, 'Virtual host'),
        vhosts.map((v) => {
          const o = typeof v === 'string' ? { id: v } : v;
          return React.createElement(MenuItem, {
            key: o.id, active: o.id === vhost, label: o.id, sub: o.sub,
            onClick: () => { setVhostOpen(false); onVhostChange && onVhostChange(o.id); },
          });
        }))),
    React.createElement('div', { style: { flex: 1 } }),
    React.createElement('span', { style: { fontSize: 13, color: 'var(--slate-500)', whiteSpace: 'nowrap' } }, 'Last refreshed: ', lastRefreshed),
    React.createElement(Button, { variant: 'secondary', size: 'sm', icon: 'refresh-cw', onClick: onRefresh }, 'Refresh'),
    React.createElement(IconButton, { icon: theme === 'dark' ? 'sun' : 'moon', bordered: false, size: 34, onClick: onThemeToggle }),
    React.createElement(IconButton, { icon: 'bell', bordered: false, size: 34, badge: notifications, onClick: onBell }),
    userName && React.createElement('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 13.5, fontWeight: 600, color: 'var(--slate-700)', whiteSpace: 'nowrap' } },
      userName, React.createElement(Icon, { name: 'chevron-down', size: 14, color: 'var(--slate-400)' }))
  );
}
