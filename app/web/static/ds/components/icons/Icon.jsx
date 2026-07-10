import React from 'react';

const pascal = (n) => n.split('-').map((s) => s.charAt(0).toUpperCase() + s.slice(1)).join('');

/** Lucide outline icon. Requires the lucide UMD script (https://unpkg.com/lucide@latest) on the page. */
export function Icon({ name, size = 18, color = 'currentColor', strokeWidth = 1.75, style }) {
  const ref = React.useRef(null);
  React.useEffect(() => {
    const L = window.lucide;
    if (!L || !ref.current) return;
    ref.current.innerHTML = '';
    const node = (L.icons && L.icons[pascal(name)]) || L[pascal(name)];
    if (!node) return;
    const el = L.createElement(node);
    el.setAttribute('width', String(size));
    el.setAttribute('height', String(size));
    el.setAttribute('stroke-width', String(strokeWidth));
    ref.current.appendChild(el);
  }, [name, size, strokeWidth]);
  return React.createElement('span', {
    ref,
    'aria-hidden': true,
    style: { display: 'inline-flex', flex: 'none', width: size, height: size, color, ...style },
  });
}
