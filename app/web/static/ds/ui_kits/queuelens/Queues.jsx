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
    const [typeFilter, setTypeFilter] = React.useState('All Types');
    const [statusFilter, setStatusFilter] = React.useState('All Statuses');
    const [consumersFilter, setConsumersFilter] = React.useState('All');
    const [sortBy, setSortBy] = React.useState('Messages (desc)');
    const STATUS_LABEL = { 'Needs Attention': 'attention', Warning: 'attention', Low: 'low', Active: 'active', Idle: 'idle', Parking: 'parking' };
    const counts = {
      all: D.queues.length,
      dlq: D.queues.filter((q) => q.type !== 'NORMAL').length,
      msg: D.queues.filter((q) => q.messages > 0).length,
      noc: D.queues.filter((q) => q.consumers === 0).length,
    };
    const totals = {
      ready: D.queues.reduce((sum, q) => sum + q.ready, 0),
      withConsumers: D.queues.filter((q) => q.consumers > 0).length,
    };
    const filtered = D.queues.filter((q) => {
      if (search && !q.name.includes(search.toLowerCase())) return false;
      if (typeFilter !== 'All Types' && q.type !== typeFilter) return false;
      if (statusFilter !== 'All Statuses' && q.status !== STATUS_LABEL[statusFilter]) return false;
      if (consumersFilter === 'Yes' && q.consumers === 0) return false;
      if (consumersFilter === 'No' && q.consumers > 0) return false;
      if (tab === 'dlq') return q.type !== 'NORMAL';
      if (tab === 'msg') return q.messages > 0;
      if (tab === 'noc') return q.consumers === 0;
      return true;
    }).sort((a, b) => {
      if (sortBy === 'Messages (asc)') return a.messages - b.messages;
      if (sortBy === 'Name') return a.name.localeCompare(b.name);
      return b.messages - a.messages;
    });
    const PAGE_SIZE = 10;
    const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    const safePage = Math.min(page, pageCount);
    const pageRows = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);
    return (
      <div>
        <PageHeader title="Queues" subtitle="Browse and inspect queues. Message counts are real-time from RabbitMQ Management API." />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(215px, 1fr))', gap: 14, marginBottom: 22 }}>
          <StatCard icon="database" tone="info" value={String(counts.all)} label="Total Queues" sublabel="All queues" />
          <StatCard icon="inbox" tone="park" value={String(counts.dlq)} label="DLQ Queues" sublabel="Dead-letter queues" />
          <StatCard icon="bar-chart-3" tone="warning" value={String(totals.ready)} label="Messages Ready" sublabel="Across all queues" />
          <StatCard icon="activity" tone="success" value={String(totals.withConsumers)} label="Queues with Consumers" sublabel="Receiving messages" />
          <StatCard icon="mail-warning" tone="danger" value={String(counts.msg)} label="Queues with Messages" sublabel="Ready to process" />
        </div>

        <div style={{ display: 'flex', gap: 14, alignItems: 'flex-end', marginBottom: 20 }}>
          <div style={{ width: 220 }}><SearchInput placeholder="Search queues…" value={search} onChange={setSearch} /></div>
          <div style={{ width: 140 }}><FilterLabel>Type</FilterLabel><Select options={['All Types', 'DLQ', 'PARKING', 'NORMAL']} value={typeFilter} onChange={setTypeFilter} /></div>
          <div style={{ width: 150 }}><FilterLabel>Status</FilterLabel><Select options={['All Statuses', 'Needs Attention', 'Low', 'Active', 'Idle', 'Parking']} value={statusFilter} onChange={setStatusFilter} /></div>
          <div style={{ width: 120 }}><FilterLabel>Has Consumers</FilterLabel><Select options={['All', 'Yes', 'No']} value={consumersFilter} onChange={setConsumersFilter} /></div>
          <div style={{ width: 170 }}><FilterLabel>Sort By</FilterLabel><Select options={['Messages (desc)', 'Messages (asc)', 'Name']} value={sortBy} onChange={setSortBy} /></div>
          <div style={{ flex: 1 }} />
          <Button variant="ghost" onClick={() => { setSearch(''); setTypeFilter('All Types'); setStatusFilter('All Statuses'); setConsumersFilter('All'); setSortBy('Messages (desc)'); }}>Clear Filters</Button>
          <Button icon="refresh-cw" onClick={() => location.reload()}>Refresh</Button>
        </div>

        <Card pad={false}>
          <div style={{ padding: '14px 20px 0' }}>
            <Tabs active={tab} onChange={(t) => { setTab(t); setPage(1); }} tabs={[
              { id: 'all', label: 'All Queues', count: counts.all },
              { id: 'dlq', label: 'DLQ Queues', count: counts.dlq },
              { id: 'msg', label: 'Queues with Messages', count: counts.msg },
              { id: 'noc', label: 'Queues with No Consumers', count: counts.noc }]} />
          </div>
          <DataTable rowKey="name" sortKey="messages"
            columns={[
              { key: 'name', label: 'Queue Name', render: (r) => <a href="#" onClick={(e) => { e.preventDefault(); nav('messages', { queue: r.name }); }} style={{ color: 'var(--text-link)', fontWeight: 600, textDecoration: 'none' }}>{r.name}</a> },
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
            rows={pageRows} />
          <div style={{ display: 'flex', alignItems: 'center', padding: '14px 20px', borderTop: '1px solid var(--slate-100)' }}>
            <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>Showing {filtered.length ? (safePage - 1) * PAGE_SIZE + 1 : 0} to {Math.min(safePage * PAGE_SIZE, filtered.length)} of {filtered.length} queues</span>
            <div style={{ flex: 1 }} />
            <Pagination page={safePage} pageCount={pageCount} onChange={setPage} />
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
