import React from 'react';
import { Icon } from '../icons/Icon.jsx';

/**
 * QueueLens data table. columns: [{key, label, align?, width?, render?(row)}].
 * Grey header row, 1px separators, hover tint, optional sort indicator + row click.
 */
export function DataTable({ columns = [], rows = [], rowKey, sortKey, sortDir = 'desc', onRowClick, selectedKey, footer, style }) {
  const [hoverRow, setHoverRow] = React.useState(null);
  return React.createElement('div', { style: { fontFamily: 'var(--font-ui)', overflowX: 'auto', ...style } },
    React.createElement('table', { style: { width: '100%', borderCollapse: 'collapse' } },
      React.createElement('thead', null,
        React.createElement('tr', { style: { background: 'var(--surface-table-header)' } },
          columns.map((c) => React.createElement('th', {
            key: c.key,
            style: {
              textAlign: c.align || 'left', padding: '10px 14px', fontSize: 12.5, fontWeight: 600,
              color: 'var(--slate-500)', whiteSpace: 'nowrap', width: c.width,
              borderBottom: '1px solid var(--border-default)',
            },
          },
            React.createElement('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 4 } },
              c.label,
              c.key === sortKey && React.createElement(Icon, { name: sortDir === 'desc' ? 'arrow-down' : 'arrow-up', size: 13, color: 'var(--slate-400)' })))))),
      React.createElement('tbody', null,
        rows.map((row, i) => {
          const k = rowKey ? row[rowKey] : i;
          const isHover = hoverRow === k, isSel = selectedKey != null && k === selectedKey;
          return React.createElement('tr', {
            key: k,
            onMouseEnter: () => setHoverRow(k), onMouseLeave: () => setHoverRow(null),
            onClick: () => onRowClick && onRowClick(row),
            style: {
              background: isSel ? 'var(--blue-50)' : isHover && onRowClick ? 'var(--slate-50)' : 'transparent',
              cursor: onRowClick ? 'pointer' : 'default',
            },
          }, columns.map((c) => React.createElement('td', {
            key: c.key,
            style: {
              textAlign: c.align || 'left', padding: '13px 14px', fontSize: 13.5,
              color: 'var(--slate-600)', borderBottom: '1px solid var(--slate-100)', whiteSpace: 'nowrap',
            },
          }, c.render ? c.render(row) : row[c.key])));
        }))),
    footer && React.createElement('div', { style: { padding: '12px 14px 2px', fontSize: 13, color: 'var(--text-muted)' } }, footer)
  );
}
