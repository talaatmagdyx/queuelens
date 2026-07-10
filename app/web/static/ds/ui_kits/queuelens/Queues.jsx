// Queues screen.
(function () {
  const { Badge, StatusPill, StatCard, Button, IconButton, Alert, DataTable, Tabs, Select, SearchInput, Pagination } = window.__NS;
  const { PageHeader, Card, ArrowLink, STATUS, TYPE_TONE } = window.QL;
  const D = window.QL.data;

  function FilterLabel({ children }) {
    return <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--slate-600)', marginBottom: 6 }}>{children}</div>;
  }

  function Queues({ nav }) {
    const [tab, setTab] = React.useState('all');
    const [page, setPage] = React.useState(1);
    const [search, setSearch] = React.useState('');
    const filtered = D.queues.filter((q) => {
      if (search && !q.name.includes(search.toLowerCase())) return false;
      if (tab === 'dlq') return q.type !== 'NORMAL';
      if (tab === 'msg') return q.messages > 0;
      if (tab === 'noc') return q.consumers === 0;
      return true;
    });
    return (
      <div>
        <PageHeader title="Queues" subtitle="Browse and inspect queues. Message counts are real-time from RabbitMQ Management API." />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(215px, 1fr))', gap: 14, marginBottom: 22 }}>
          <StatCard icon="database" tone="info" value="24" label="Total Queues" sublabel="All queues" />
          <StatCard icon="inbox" tone="park" value="4" label="DLQ Queues" sublabel="Dead-letter queues" />
          <StatCard icon="bar-chart-3" tone="warning" value="128" label="Messages Ready" sublabel="Across all queues" />
          <StatCard icon="activity" tone="success" value="18" label="Queues with Consumers" sublabel="Receiving messages" />
          <StatCard icon="mail-warning" tone="danger" value="6" label="Queues with Messages" sublabel="Ready to process" />
        </div>

        <div style={{ display: 'flex', gap: 14, alignItems: 'flex-end', marginBottom: 20 }}>
          <div style={{ width: 220 }}><SearchInput placeholder="Search queues…" value={search} onChange={setSearch} /></div>
          <div style={{ width: 140 }}><FilterLabel>Type</FilterLabel><Select options={['All Types', 'DLQ', 'PARKING', 'NORMAL']} /></div>
          <div style={{ width: 150 }}><FilterLabel>Status</FilterLabel><Select options={['All Statuses', 'Needs Attention', 'Low', 'Active', 'Idle']} /></div>
          <div style={{ width: 120 }}><FilterLabel>Has Consumers</FilterLabel><Select options={['All', 'Yes', 'No']} /></div>
          <div style={{ width: 170 }}><FilterLabel>Sort By</FilterLabel><Select options={['Messages (desc)', 'Messages (asc)', 'Name']} /></div>
          <div style={{ flex: 1 }} />
          <Button variant="ghost" onClick={() => setSearch('')}>Clear Filters</Button>
          <Button icon="refresh-cw">Refresh</Button>
        </div>

        <Card pad={false}>
          <div style={{ padding: '14px 20px 0' }}>
            <Tabs active={tab} onChange={(t) => { setTab(t); setPage(1); }} tabs={[
              { id: 'all', label: 'All Queues', count: 24 },
              { id: 'dlq', label: 'DLQ Queues', count: 4 },
              { id: 'msg', label: 'Queues with Messages', count: 6 },
              { id: 'noc', label: 'Queues with No Consumers', count: 6 }]} />
          </div>
          <DataTable rowKey="name" sortKey="messages"
            columns={[
              { key: 'name', label: 'Queue Name', render: (r) => <a href="#" onClick={(e) => { e.preventDefault(); nav('queuedetail', { queue: r.name }); }} style={{ color: 'var(--text-link)', fontWeight: 600, textDecoration: 'none' }}>{r.name}</a> },
              { key: 'type', label: 'Type', render: (r) => (
                <span style={{ display: 'inline-flex', gap: 6 }}>
                  <Badge tone={TYPE_TONE[r.type]}>{r.type}</Badge>
                  {r.retry && <Badge tone="warning" uppercase={false}>retry</Badge>}
                  {r.qtype && r.qtype !== 'classic' && <Badge tone={r.qtype === 'quorum' ? 'park' : 'info'} uppercase={false}>{r.qtype}</Badge>}
                </span>) },
              { key: 'messages', label: 'Messages', render: (r) => <span style={{ fontWeight: 600, color: r.status === 'attention' ? 'var(--red-600)' : 'var(--slate-700)' }}>{r.messages}</span> },
              { key: 'ready', label: 'Ready' },
              { key: 'unacked', label: 'Unacked' },
              { key: 'consumers', label: 'Consumers' },
              { key: 'status', label: 'Status', render: (r) => { const s = STATUS[r.status]; return <StatusPill tone={s.tone} dot={s.dot}>{s.label}</StatusPill>; } },
              { key: 'last', label: 'Last Message' },
              { key: 'a', label: 'Actions', align: 'right', render: (r) => (
                <span style={{ display: 'inline-flex', gap: 8 }}>
                  <Button variant="secondary" size="sm" onClick={() => nav('messages', { queue: r.name })} style={{ color: 'var(--text-link)' }}>View</Button>
                  <IconButton icon="ellipsis-vertical" />
                </span>) },
            ]}
            rows={filtered} />
          <div style={{ display: 'flex', alignItems: 'center', padding: '14px 20px', borderTop: '1px solid var(--slate-100)' }}>
            <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>Showing 1 to {filtered.length} of 24 queues</span>
            <div style={{ flex: 1 }} />
            <Pagination page={page} pageCount={3} onChange={setPage} />
          </div>
        </Card>

        <Alert tone="info" style={{ marginTop: 20 }} action={<ArrowLink onClick={() => {}}>Learn more</ArrowLink>}>
          Counts are from RabbitMQ Management API and update automatically. Message preview is limited to 100 per queue.
        </Alert>
      </div>
    );
  }

  window.QL.screens.Queues = Queues;
})();
