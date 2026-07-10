import React from 'react';

const TONES = {
  danger:  { color: 'var(--red-600)',    border: 'var(--red-200)' },
  success: { color: 'var(--green-600)',  border: 'var(--green-200)' },
  warning: { color: 'var(--amber-600)',  border: 'var(--amber-200)' },
  info:    { color: 'var(--blue-600)',   border: 'var(--blue-200)' },
  park:    { color: 'var(--purple-600)', border: 'var(--purple-100)' },
  data:    { color: 'var(--magenta-600)',border: 'var(--magenta-100)' },
  neutral: { color: 'var(--slate-500)',  border: 'var(--slate-300)' },
};

/** Outlined ALL-CAPS type badge: DLQ, PARKING, NORMAL, JSON, TEXT, BASE64, retry, DEVELOPMENT. */
export function Badge({ tone = 'neutral', children, uppercase = true, style }) {
  const t = TONES[tone] || TONES.neutral;
  return React.createElement('span', {
    style: {
      display: 'inline-flex', alignItems: 'center', height: 22, padding: '0 8px',
      fontFamily: 'var(--font-ui)', fontSize: 11, fontWeight: 600,
      textTransform: uppercase ? 'uppercase' : 'none', letterSpacing: uppercase ? '0.04em' : 0,
      color: t.color, background: 'transparent', border: `1px solid ${t.border}`,
      borderRadius: 'var(--radius-sm)', whiteSpace: 'nowrap', ...style,
    },
  }, children);
}
