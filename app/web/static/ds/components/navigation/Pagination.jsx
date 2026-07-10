import React from 'react';
import { Icon } from '../icons/Icon.jsx';

function PageBtn({ children, active, disabled, onClick }) {
  const [hover, setHover] = React.useState(false);
  return React.createElement('button', {
    onClick, disabled,
    onMouseEnter: () => setHover(true), onMouseLeave: () => setHover(false),
    style: {
      minWidth: 32, height: 32, padding: '0 6px', borderRadius: 'var(--radius-md)',
      border: `1px solid ${active ? 'var(--blue-200)' : 'var(--border-default)'}`,
      background: active ? 'var(--blue-50)' : hover && !disabled ? 'var(--slate-100)' : 'var(--surface-control)',
      color: active ? 'var(--blue-600)' : 'var(--slate-600)',
      fontFamily: 'var(--font-ui)', fontSize: 13, fontWeight: 600,
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.45 : 1,
    },
  }, children);
}

/** Page number strip with prev/next chevrons and optional ellipsis. */
export function Pagination({ page = 1, pageCount = 1, onChange, style }) {
  const go = (p) => onChange && onChange(Math.min(pageCount, Math.max(1, p)));
  const pages = [];
  if (pageCount <= 7) { for (let i = 1; i <= pageCount; i++) pages.push(i); }
  else {
    for (let i = 1; i <= 5; i++) pages.push(i);
    pages.push('…', pageCount);
  }
  return React.createElement('div', { style: { display: 'flex', gap: 6, alignItems: 'center', ...style } },
    React.createElement(PageBtn, { disabled: page <= 1, onClick: () => go(page - 1) }, React.createElement(Icon, { name: 'chevron-left', size: 15 })),
    pages.map((p, i) => p === '…'
      ? React.createElement('span', { key: 'e' + i, style: { color: 'var(--slate-400)', fontFamily: 'var(--font-ui)', fontSize: 13, padding: '0 2px' } }, '…')
      : React.createElement(PageBtn, { key: p, active: p === page, onClick: () => go(p) }, p)),
    React.createElement(PageBtn, { disabled: page >= pageCount, onClick: () => go(page + 1) }, React.createElement(Icon, { name: 'chevron-right', size: 15 }))
  );
}
