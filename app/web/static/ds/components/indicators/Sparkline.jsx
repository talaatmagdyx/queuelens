import React from 'react';

/** Tiny green sparkline used in the sidebar Broker Live card. */
export function Sparkline({ points = [4, 8, 5, 14, 6, 9, 5], width = 64, height = 28, color = 'var(--green-600)', style }) {
  const max = Math.max(...points), min = Math.min(...points);
  const range = max - min || 1;
  const step = width / (points.length - 1);
  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${(height - 3 - ((p - min) / range) * (height - 6)).toFixed(1)}`).join(' ');
  return React.createElement('svg', { width, height, viewBox: `0 0 ${width} ${height}`, style: { display: 'block', ...style } },
    React.createElement('path', { d, fill: 'none', stroke: color, strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round' })
  );
}
