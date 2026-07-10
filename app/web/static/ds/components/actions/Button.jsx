import React from 'react';
import { Icon } from '../icons/Icon.jsx';

const VARIANTS = {
  primary:   { bg: 'var(--blue-600)',   hover: 'var(--blue-700)',   color: '#fff',               border: '1px solid transparent' },
  secondary: { bg: 'var(--surface-control)', hover: 'var(--slate-100)',   color: 'var(--slate-700)',   border: '1px solid var(--border-default)' },
  danger:    { bg: 'var(--surface-control)',              hover: 'var(--red-50)',     color: 'var(--red-600)',     border: '1px solid var(--red-200)' },
  dangerSolid:{ bg: 'var(--red-600)',   hover: 'var(--red-700)',    color: '#fff',               border: '1px solid transparent' },
  success:   { bg: 'var(--green-50)',   hover: 'var(--green-100)',  color: 'var(--green-700)',   border: '1px solid var(--green-200)' },
  park:      { bg: 'var(--surface-control)',              hover: 'var(--purple-50)',  color: 'var(--purple-600)',  border: '1px solid var(--purple-100)' },
  ghost:     { bg: 'transparent',       hover: 'var(--slate-100)',  color: 'var(--slate-600)',   border: '1px solid transparent' },
};

const SIZES = {
  sm: { height: 30, padding: '0 12px', fontSize: 13, icon: 14 },
  md: { height: 38, padding: '0 16px', fontSize: 14, icon: 16 },
  lg: { height: 42, padding: '0 20px', fontSize: 14, icon: 16 },
};

export function Button({ variant = 'primary', size = 'md', icon, iconRight, children, disabled, onClick, style }) {
  const [hover, setHover] = React.useState(false);
  const v = VARIANTS[variant] || VARIANTS.primary;
  const s = SIZES[size] || SIZES.md;
  return React.createElement(
    'button',
    {
      onClick,
      disabled,
      onMouseEnter: () => setHover(true),
      onMouseLeave: () => setHover(false),
      style: {
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8,
        height: s.height, padding: s.padding, fontSize: s.fontSize,
        fontFamily: 'var(--font-ui)', fontWeight: 600, whiteSpace: 'nowrap',
        background: hover && !disabled ? v.hover : v.bg, color: v.color, border: v.border,
        borderRadius: 'var(--radius-md)', cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.5 : 1, transition: 'background var(--duration-fast) var(--ease-out)',
        ...style,
      },
    },
    icon && React.createElement(Icon, { name: icon, size: s.icon }),
    children,
    iconRight && React.createElement(Icon, { name: iconRight, size: s.icon })
  );
}
