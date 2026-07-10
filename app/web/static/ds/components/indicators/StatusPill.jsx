import React from 'react';

const TONES = {
  success: { bg: 'var(--green-100)',  color: 'var(--green-700)',  dot: 'var(--green-600)' },
  danger:  { bg: 'var(--red-100)',    color: 'var(--red-600)',    dot: 'var(--red-600)' },
  warning: { bg: 'var(--amber-100)',  color: 'var(--amber-700)',  dot: 'var(--amber-600)' },
  info:    { bg: 'var(--blue-50)',    color: 'var(--blue-600)',   dot: 'var(--blue-600)' },
  park:    { bg: 'var(--purple-100)', color: 'var(--purple-600)', dot: 'var(--purple-600)' },
  neutral: { bg: 'var(--slate-100)',  color: 'var(--slate-500)',  dot: 'var(--slate-400)' },
};

/** Filled status pill: Success, Failed, Needs Attention, Low, Active, Idle, Parking, Healthy… */
export function StatusPill({ tone = 'neutral', dot = false, children, size = 'md', style }) {
  const t = TONES[tone] || TONES.neutral;
  const sm = size === 'sm';
  return React.createElement('span', {
    style: {
      display: 'inline-flex', alignItems: 'center', gap: 6,
      height: sm ? 20 : 24, padding: sm ? '0 8px' : '0 10px',
      fontFamily: 'var(--font-ui)', fontSize: sm ? 11 : 12.5, fontWeight: 600,
      color: t.color, background: t.bg, borderRadius: 'var(--radius-sm)', whiteSpace: 'nowrap', ...style,
    },
  },
    dot && React.createElement('span', { style: { width: 7, height: 7, borderRadius: 999, background: t.dot, flex: 'none' } }),
    children
  );
}
