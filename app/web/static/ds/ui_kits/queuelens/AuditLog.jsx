// Audit Log screen with Action Details panel.
(function () {
  const { Icon, Badge, StatusPill, StatCard, Button, IconButton, DataTable, Select, SearchInput, Input, Pagination, KeyValue, CodeBlock } = window.__NS;
  const { PageHeader, Card, XDeathTable, ACTION_META, RESULT_TONE } = window.QL;
  const D = window.QL.data;

  function AuditLog({ nav }) {
    const [sel, setSel] = React.useState(D.audit[0]);
    const [page, setPage] = React.useState(1);
    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageHeader title="Audit Log" subtitle="Complete history of all actions performed in QueueLens."
            actions={<Button variant="secondary" icon="download">Export CSV</Button>} />

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(215px, 1fr))', gap: 14, marginBottom: 20 }}>
            <StatCard icon="database" tone="info" value="152" label="Total Actions" sublabel="All time" />
            <StatCard icon="check-circle" tone="success" value="145" label="Successful" sublabel="95.4%" />
            <StatCard icon="x-circle" tone="danger" value="5" label="Failed" sublabel="3.3%" />
            <StatCard icon="clock" tone="warning" value="2" label="In Progress" sublabel="1.3%" />
            <StatCard icon="users" tone="park" value="18" label="Unique Users" sublabel="All time" />
          </div>

          <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
            <div style={{ flex: 1 }}><SearchInput placeholder="Search by queue, user, action…" /></div>
            <div style={{ width: 140 }}><Select options={['All Actions', 'Replay (Move)', 'Replay (Copy)', 'Park', 'Delete']} /></div>
            <div style={{ width: 130 }}><Select options={['All Results', 'Success', 'Failed']} /></div>
            <div style={{ width: 130 }}><Input placeholder="From date" /></div>
            <div style={{ width: 130 }}><Input placeholder="To date" /></div>
          </div>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginBottom: 18 }}>
            <Button variant="ghost">Clear Filters</Button>
            <Button>Apply Filters</Button>
          </div>

          <Card pad={false}>
            <DataTable rowKey="time" sortKey="time" onRowClick={setSel} selectedKey={sel && sel.time}
              columns={[
                { key: 'time', label: 'Time' },
                { key: 'user', label: 'User' },
                { key: 'action', label: 'Action', render: (r) => { const m = ACTION_META[r.action]; return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, color: m.color, fontWeight: 600 }}><Icon name={m.icon} size={14} />{m.label}</span>; } },
                { key: 'queue', label: 'Queue' },
                { key: 'target', label: 'Target / Destination', render: (r) => <span style={{ whiteSpace: 'normal', display: 'inline-block', maxWidth: 200 }}>{r.target}</span> },
                { key: 'result', label: 'Result', render: (r) => <StatusPill tone={RESULT_TONE[r.result]}>{r.result}</StatusPill> },
                { key: 'duration', label: 'Duration' },
                { key: 'd', label: 'Details', align: 'right', render: (r) => <IconButton icon="eye" size={28} onClick={() => setSel(r)} /> },
              ]}
              rows={D.audit} />
            <div style={{ display: 'flex', alignItems: 'center', padding: '14px 20px', borderTop: '1px solid var(--slate-100)' }}>
              <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>Showing 1 to 10 of 152 actions</span>
              <div style={{ flex: 1 }} />
              <Pagination page={page} pageCount={16} onChange={setPage} />
              <div style={{ width: 110, marginLeft: 10 }}><Select options={['10 / page', '25 / page']} /></div>
            </div>
          </Card>
        </div>

        {sel && (
          <aside style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none', background: 'var(--surface-card)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-lg)', padding: 20, boxSizing: 'border-box', position: 'sticky', top: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-heading)', flex: 1 }}>Action Details</div>
              <IconButton icon="x" bordered={false} size={28} onClick={() => setSel(null)} />
            </div>
            <StatusPill tone={RESULT_TONE[sel.result]}>{sel.result}</StatusPill>
            <div style={{ marginTop: 14 }}>
              <KeyValue gap={12} items={[
                { label: 'Action', value: (() => { const m = ACTION_META[sel.action]; return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, color: m.color, fontWeight: 600 }}><Icon name={m.icon} size={14} />{m.label}</span>; })() },
                { label: 'Message ID', value: 'a1b2c3d4-e5f6-11ee-a1b2-0242ac120002', mono: true, copy: true },
                { label: 'Source Queue', value: <Badge tone="danger" uppercase={false}>{sel.queue}</Badge> },
                { label: 'Target', value: <Badge tone="info" uppercase={false}>{sel.target}</Badge> },
                { label: 'User', value: sel.user },
                { label: 'Time', value: sel.time },
                { label: 'Duration', value: sel.duration },
                { label: 'Published First', value: 'Yes (publish-before-ack)' },
              ]} />
            </div>
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-heading)', marginBottom: 8 }}>Headers Added</div>
              <CodeBlock code={`x-queuelens-replayed: true\nx-queuelens-source-queue: ${sel.queue}\nx-queuelens-replayed-at: 2024-05-21T10:24:15.123Z\nx-queuelens-action: ${sel.action}\nx-queuelens-user: ${sel.user}`} />
            </div>
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-heading)', marginBottom: 8 }}>x-death (3)</div>
              <XDeathTable rows={D.xdeath} />
            </div>
            <Button variant="secondary" icon="eye" size="sm" style={{ marginTop: 16, color: 'var(--text-link)' }} onClick={() => nav('messages')}>View Message</Button>
          </aside>
        )}
      </div>
    );
  }

  window.QL.screens.AuditLog = AuditLog;
})();
