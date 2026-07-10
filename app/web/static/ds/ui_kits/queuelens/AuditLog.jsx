// Audit Log screen with Action Details panel — fully wired to live audit data.
(function () {
  const { Icon, Badge, StatusPill, StatCard, Button, IconButton, DataTable, Select, SearchInput, Input, Pagination, KeyValue, CodeBlock } = window.__NS;
  const { PageHeader, Card, XDeathTable, ACTION_META, RESULT_TONE } = window.QL;
  const D = window.QL.data;

  const ACTION_FILTER = { 'Replay (Move)': 'replay_move', 'Replay (Copy)': 'replay_copy', Park: 'park', Delete: 'delete' };
  const pct = (n, total) => (total ? ((n / total) * 100).toFixed(1) + '%' : '—');

  function AuditLog({ nav }) {
    // Refetch on every mount so actions executed this session show up immediately.
    const audit = React.useMemo(() => window.QL.fetchAudit(), []);
    const [sel, setSel] = React.useState(audit[0] || null);
    const [page, setPage] = React.useState(1);
    const [pageSize, setPageSize] = React.useState(10);
    const [q, setQ] = React.useState('');
    const [actionF, setActionF] = React.useState('All Actions');
    const [resultF, setResultF] = React.useState('All Results');
    const [from, setFrom] = React.useState('');
    const [to, setTo] = React.useState('');

    const rows = React.useMemo(() => audit.filter((r) => {
      if (q && !(r.queue + ' ' + r.user + ' ' + r.action + ' ' + r.target).toLowerCase().includes(q.toLowerCase())) return false;
      if (actionF !== 'All Actions' && r.action !== ACTION_FILTER[actionF]) return false;
      if (resultF !== 'All Results' && r.result !== resultF) return false;
      if (from && r.time.slice(0, 10) < from) return false;
      if (to && r.time.slice(0, 10) > to) return false;
      return true;
    }), [q, actionF, resultF, from, to]);

    const total = audit.length;
    const ok = audit.filter((r) => r.result === 'Success').length;
    const failed = audit.filter((r) => r.result === 'Failed').length;
    const started = audit.filter((r) => r.result === 'Started').length;
    const uniqueUsers = new Set(audit.map((r) => r.user)).size;

    const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
    const safePage = Math.min(page, pageCount);
    const pageRows = rows.slice((safePage - 1) * pageSize, safePage * pageSize);
    const filtersOn = q || from || to || actionF !== 'All Actions' || resultF !== 'All Results';

    const exportCsv = () => {
      const head = 'time,user,action,queue,target,result,duration';
      const esc = (v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
      const body = rows.map((r) => [r.time, r.user, r.action, r.queue, r.target, r.result, r.duration].map(esc).join(','));
      const blob = new Blob([[head].concat(body).join('\n')], { type: 'text/csv' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'queuelens-audit.csv';
      a.click();
      URL.revokeObjectURL(a.href);
    };

    const clearFilters = () => { setQ(''); setActionF('All Actions'); setResultF('All Results'); setFrom(''); setTo(''); setPage(1); };

    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageHeader title="Audit Log" subtitle="Complete history of all actions performed in QueueLens."
            actions={<Button variant="secondary" icon="download" onClick={exportCsv}>Export CSV</Button>} />

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(215px, 1fr))', gap: 14, marginBottom: 20 }}>
            <StatCard icon="database" tone="info" value={String(total)} label="Total Actions" sublabel="Last 500" />
            <StatCard icon="check-circle" tone="success" value={String(ok)} label="Successful" sublabel={pct(ok, total)} />
            <StatCard icon="x-circle" tone="danger" value={String(failed)} label="Failed" sublabel={pct(failed, total)} />
            <StatCard icon="clock" tone="warning" value={String(started)} label="In Progress" sublabel={pct(started, total)} />
            <StatCard icon="users" tone="park" value={String(uniqueUsers)} label="Unique Users" sublabel="Last 500" />
          </div>

          <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
            <div style={{ flex: 1 }}><SearchInput placeholder="Search by queue, user, action…" value={q} onChange={(v) => { setQ(v); setPage(1); }} /></div>
            <div style={{ width: 140 }}><Select value={actionF} onChange={(v) => { setActionF(v); setPage(1); }} options={['All Actions', 'Replay (Move)', 'Replay (Copy)', 'Park', 'Delete']} /></div>
            <div style={{ width: 130 }}><Select value={resultF} onChange={(v) => { setResultF(v); setPage(1); }} options={['All Results', 'Success', 'Failed']} /></div>
            <div style={{ width: 145 }}><Input type="date" value={from} onChange={(v) => { setFrom(v); setPage(1); }} /></div>
            <div style={{ width: 145 }}><Input type="date" value={to} onChange={(v) => { setTo(v); setPage(1); }} /></div>
          </div>
          {filtersOn && (
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginBottom: 18 }}>
              <Button variant="ghost" onClick={clearFilters}>Clear Filters</Button>
            </div>
          )}

          <Card pad={false}>
            <DataTable rowKey="key" sortKey="time" onRowClick={setSel} selectedKey={sel && sel.key}
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
              rows={pageRows} />
            <div style={{ display: 'flex', alignItems: 'center', padding: '14px 20px', borderTop: '1px solid var(--slate-100)' }}>
              <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                {rows.length
                  ? `Showing ${(safePage - 1) * pageSize + 1} to ${Math.min(safePage * pageSize, rows.length)} of ${rows.length} actions`
                  : 'No actions match the current filters'}
              </span>
              <div style={{ flex: 1 }} />
              <Pagination page={safePage} pageCount={pageCount} onChange={setPage} />
              <div style={{ width: 110, marginLeft: 10 }}>
                <Select value={pageSize + ' / page'} onChange={(v) => { setPageSize(parseInt(v, 10)); setPage(1); }} options={['10 / page', '25 / page']} />
              </div>
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
                sel.fingerprint ? { label: 'Fingerprint', value: sel.fingerprint.slice(0, 24) + '…', mono: true, copy: true } : null,
                { label: 'Source Queue', value: <Badge tone="danger" uppercase={false}>{sel.queue}</Badge> },
                { label: 'Target', value: <Badge tone="info" uppercase={false}>{sel.target}</Badge> },
                { label: 'User', value: sel.user },
                { label: 'Time', value: sel.time },
                { label: 'Duration', value: sel.duration },
                sel.action !== 'delete' ? { label: 'Published First', value: 'Yes (publish-before-ack)' } : null,
              ].filter(Boolean)} />
            </div>
            {sel.error && (
              <div style={{ marginTop: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--danger-600, #dc2626)', marginBottom: 8 }}>Error</div>
                <CodeBlock code={String(sel.error)} />
              </div>
            )}
            {sel.headersAdded && Object.keys(sel.headersAdded).length > 0 && (
              <div style={{ marginTop: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-heading)', marginBottom: 8 }}>Headers Added</div>
                <CodeBlock code={Object.entries(sel.headersAdded).map(([k, v]) => k + ': ' + v).join('\n')} />
              </div>
            )}
            {sel.xdeathList.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-heading)', marginBottom: 8 }}>x-death ({sel.xdeathList.length})</div>
                <XDeathTable rows={sel.xdeathList} />
              </div>
            )}
            <Button variant="secondary" icon="eye" size="sm" style={{ marginTop: 16, color: 'var(--text-link)' }} onClick={() => nav('messages', { queue: sel.queue !== '\u2014' ? sel.queue : undefined, fingerprint: sel.fingerprint || undefined })}>View Message</Button>
          </aside>
        )}
      </div>
    );
  }

  window.QL.screens.AuditLog = AuditLog;
})();
