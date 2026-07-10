import React from 'react';
import { Icon } from '../icons/Icon.jsx';

function tokenize(line) {
  // crude JSON syntax tint: keys red, strings green, numbers/booleans blue
  const parts = [];
  const re = /("(?:[^"\\]|\\.)*")(\s*:)?|(-?\d+(?:\.\d+)?)|(\btrue\b|\bfalse\b|\bnull\b)/g;
  let last = 0, m;
  while ((m = re.exec(line))) {
    if (m.index > last) parts.push({ t: 'plain', v: line.slice(last, m.index) });
    if (m[1] && m[2]) { parts.push({ t: 'key', v: m[1] }); parts.push({ t: 'plain', v: m[2] }); }
    else if (m[1]) parts.push({ t: 'str', v: m[1] });
    else if (m[3]) parts.push({ t: 'num', v: m[3] });
    else if (m[4]) parts.push({ t: 'num', v: m[4] });
    last = re.lastIndex;
  }
  if (last < line.length) parts.push({ t: 'plain', v: line.slice(last) });
  return parts;
}
const COLORS = { key: 'var(--red-600)', str: 'var(--green-700)', num: 'var(--blue-600)', plain: 'var(--slate-700)' };

/** Line-numbered code block with light JSON syntax tint and optional copy button. */
export function CodeBlock({ code = '', lineNumbers = true, copy = false, maxHeight, fontSize = 12.5, style }) {
  const lines = String(code).split('\n');
  return React.createElement('div', {
    style: {
      position: 'relative', background: 'var(--slate-50)', border: '1px solid var(--border-default)',
      borderRadius: 'var(--radius-md)', overflow: 'auto', maxHeight, fontFamily: 'var(--font-mono)', fontSize,
      lineHeight: 1.75, ...style,
    },
  },
    copy && React.createElement('span', { style: { position: 'sticky', top: 8, float: 'right', marginRight: 8, cursor: 'pointer' } },
      React.createElement(Icon, { name: 'copy', size: 14, color: 'var(--slate-400)' })),
    React.createElement('table', { style: { borderCollapse: 'collapse', width: '100%' } },
      React.createElement('tbody', null,
        lines.map((line, i) => React.createElement('tr', { key: i },
          lineNumbers && React.createElement('td', {
            style: { userSelect: 'none', textAlign: 'right', padding: '0 10px 0 12px', color: 'var(--slate-400)', borderRight: '1px solid var(--border-default)', width: 1, verticalAlign: 'top' },
          }, i + 1),
          React.createElement('td', { style: { padding: '0 14px', whiteSpace: 'pre' } },
            tokenize(line).map((p, j) => React.createElement('span', { key: j, style: { color: COLORS[p.t] } }, p.v)))))))
  );
}
