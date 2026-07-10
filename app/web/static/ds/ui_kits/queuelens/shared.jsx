// Shared kit pieces: page header, card, breadcrumbs, message summary panel.
(function () {
  const NS = window.__NS;
  const { Icon, Badge, StatusPill, KeyValue, CodeBlock, Tabs } = NS;

  function PageHeader({ title, subtitle, after, actions }) {
    return (
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 20 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <h1 style={{ margin: 0, fontSize: 32, fontWeight: 700, color: 'var(--text-heading)', lineHeight: 1.2 }}>{title}</h1>
            {after}
          </div>
          {subtitle && <p style={{ margin: '6px 0 0', fontSize: 14, color: 'var(--text-body)' }}>{subtitle}</p>}
        </div>
        {actions}
      </div>
    );
  }

  function Card({ title, subtitle, action, children, style, pad = true }) {
    return (
      <section style={{ background: 'var(--surface-card)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-lg)', ...style }}>
        {(title || action) && (
          <div style={{ display: 'flex', alignItems: 'flex-start', padding: '18px 20px 12px' }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-heading)' }}>{title}</div>
              {subtitle && <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 2 }}>{subtitle}</div>}
            </div>
            {action}
          </div>
        )}
        <div style={{ padding: pad ? '0 20px 18px' : 0 }}>{children}</div>
      </section>
    );
  }

  function ArrowLink({ children, onClick }) {
    return (
      <a href="#" onClick={(e) => { e.preventDefault(); onClick && onClick(); }}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 13.5, fontWeight: 600, color: 'var(--text-link)', textDecoration: 'none', whiteSpace: 'nowrap' }}>
        {children} <Icon name="arrow-right" size={14} />
      </a>
    );
  }

  function Breadcrumbs({ items }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13.5, marginBottom: 10 }}>
        {items.map((it, i) => (
          <React.Fragment key={i}>
            {i > 0 && <Icon name="chevron-right" size={13} color="var(--slate-300)" />}
            {it.onClick
              ? <a href="#" onClick={(e) => { e.preventDefault(); it.onClick(); }} style={{ color: 'var(--slate-500)', textDecoration: 'none', fontWeight: 500 }}>{it.label}</a>
              : <span style={{ color: i === items.length - 1 ? 'var(--slate-900)' : 'var(--slate-400)', fontWeight: i === items.length - 1 ? 600 : 500 }}>{it.label}</span>}
          </React.Fragment>
        ))}
      </div>
    );
  }

  const PAYLOAD_TONE = { JSON: 'success', TEXT: 'neutral', BASE64: 'data' };

  // Right-hand Message Summary panel (Replay / Park wizards)
  function MessageSummaryPanel({ msg, payload, xdeath }) {
    const [tab, setTab] = React.useState('payload');
    return (
      <Card title="Message Summary" style={{ position: 'sticky', top: 16 }}>
        <KeyValue gap={12} items={[
          { label: 'Message ID', value: msg.id, mono: true, copy: true },
          { label: 'Published At', value: msg.at },
          { label: 'Size', value: msg.size },
          { label: 'Payload Type', value: <Badge tone={PAYLOAD_TONE[msg.type] || 'neutral'}>{msg.type}</Badge> },
          { label: 'x-death Count', value: String(msg.xdeath) },
          { label: 'Source Queue', value: <Badge tone="danger" uppercase={false}>payments.retry.dlq</Badge> },
        ]} />
        <div style={{ marginTop: 16 }}>
          <Tabs active={tab} onChange={setTab} tabs={[
            { id: 'payload', label: 'Payload' }, { id: 'headers', label: 'Headers' },
            { id: 'props', label: 'Properties' }, { id: 'xdeath', label: 'x-death' }]} style={{ gap: 18 }} />
          <div style={{ marginTop: 12 }}>
            {tab === 'payload' && <CodeBlock code={payload} copy maxHeight={280} />}
            {tab === 'headers' && <CodeBlock code={'x-death: [3 entries]\nx-first-death-exchange: email.exchange\nx-first-death-queue: email.retry\nx-first-death-reason: rejected'} maxHeight={280} />}
            {tab === 'props' && <CodeBlock code={'content_type: application/json\ndelivery_mode: 2 (persistent)\npriority: 0\nmessage_id: a1b2c3d4-e5f6-11ee'} maxHeight={280} />}
            {tab === 'xdeath' && <XDeathTable rows={xdeath} />}
          </div>
        </div>
        <div style={{ marginTop: 18 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-heading)', marginBottom: 8 }}>x-death Details ({xdeath.length})</div>
          <XDeathTable rows={xdeath} />
        </div>
      </Card>
    );
  }

  function XDeathTable({ rows }) {
    const th = { textAlign: 'left', fontSize: 12, fontWeight: 600, color: 'var(--slate-500)', padding: '6px 8px 6px 0' };
    const td = { fontSize: 12.5, color: 'var(--slate-600)', padding: '6px 8px 6px 0', borderTop: '1px solid var(--slate-100)' };
    return (
      <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-ui)' }}>
        <thead><tr><th style={th}>#</th><th style={th}>Reason</th><th style={th}>Queue</th><th style={th}>Count</th><th style={th}>Time</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.n}><td style={td}>{r.n}</td><td style={td}>{r.reason}</td><td style={td}>{r.queue}</td><td style={td}>{r.count}</td><td style={td}>{r.time}</td></tr>
          ))}
        </tbody>
      </table>
    );
  }

  // Centered empty / offline state
  function EmptyState({ icon = 'check-circle', tone = 'success', title, children, actions }) {
    const tones = { success: ['var(--green-100)', 'var(--green-600)'], danger: ['var(--red-100)', 'var(--red-600)'], info: ['var(--blue-50)', 'var(--blue-600)'], neutral: ['var(--slate-100)', 'var(--slate-500)'] };
    const pair = tones[tone] || tones.neutral;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', padding: '44px 24px' }}>
        <div style={{ width: 56, height: 56, borderRadius: 999, background: pair[0], display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Icon name={icon} size={26} color={pair[1]} />
        </div>
        <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-heading)', marginTop: 14 }}>{title}</div>
        {children && <div style={{ fontSize: 13.5, color: 'var(--text-muted)', marginTop: 6, maxWidth: 440, lineHeight: 1.6 }}>{children}</div>}
        {actions && <div style={{ display: 'flex', gap: 10, marginTop: 18, flexWrap: 'wrap', justifyContent: 'center' }}>{actions}</div>}
      </div>
    );
  }

  window.QL = window.QL || {};
  Object.assign(window.QL, { PageHeader, Card, ArrowLink, Breadcrumbs, MessageSummaryPanel, XDeathTable, EmptyState, PAYLOAD_TONE });
})();
