import React from 'react';
import { Icon } from '../icons/Icon.jsx';

/** Square bordered (or borderless) icon-only button: kebab menus, eye/view, copy, close. */
export function IconButton({ icon, size = 32, iconSize, bordered = true, color = 'var(--slate-500)', badge, onClick, title, style }) {
  const [hover, setHover] = React.useState(false);
  return React.createElement(
    'button',
    {
      onClick, title,
      onMouseEnter: () => setHover(true),
      onMouseLeave: () => setHover(false),
      style: {
        position: 'relative', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: size, height: size, flex: 'none',
        background: hover ? 'var(--slate-100)' : bordered ? 'var(--surface-control)' : 'transparent',
        border: bordered ? '1px solid var(--border-default)' : '1px solid transparent',
        borderRadius: 'var(--radius-md)', color, cursor: 'pointer',
        transition: 'background var(--duration-fast) var(--ease-out)', ...style,
      },
    },
    React.createElement(Icon, { name: icon, size: iconSize || Math.round(size * 0.5) }),
    badge != null && React.createElement('span', {
      style: {
        position: 'absolute', top: -5, right: -5, minWidth: 16, height: 16, padding: '0 4px',
        borderRadius: 999, background: 'var(--red-600)', color: '#fff', fontSize: 10, fontWeight: 700,
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--font-ui)',
      },
    }, badge)
  );
}
