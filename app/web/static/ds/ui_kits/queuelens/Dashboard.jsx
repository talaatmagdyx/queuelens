// Dashboard screen.
(function () {
  const { Icon, Badge, StatusPill, StatCard, Button, Alert, DataTable } = window.__NS;
  const { PageHeader, Card, ArrowLink, EmptyState } = window.QL;
  const D = window.QL.data;

  const STATUS = {
    attention: { tone: 'danger', dot: true, label: 'Needs Attention' },
    parking: { tone: 'success', dot: true, label: 'Parking' },
    low: { tone: 'warning', dot: true, label: 'Low' },
    active: { tone: 'success', label: 'Active' },
    idle: { tone: 'neutral', label: 'Idle' },
  };
  const TYPE_TONE = { DLQ: 'danger', PARKING: 'success', NORMAL: 'info' };
  const ACTION_META = {
    replay_move: { icon: 'play', color: 'var(--blue-600)', label: 'Replay (Move)' },
    replay_copy: { icon: 'copy', color: 'var(--blue-600)', label: 'Replay (Copy)' },
    park: { icon: 'flag', color: 'var(--purple-600)', label: 'Park' },
    delete: { icon: 'trash-2', color: 'var(--red-600)', label: 'Delete' },
    publish: { icon: 'send', color: 'var(--green-700)', label: 'Publish (Test)' },
    invite_user: { icon: 'user-plus', color: 'var(--slate-700)', label: 'User Invited' },
    update_user: { icon: 'user-cog', color: 'var(--slate-700)', label: 'User Changed' },
    delete_user: { icon: 'user-x', color: 'var(--red-600)', label: 'User Removed' },
    change_password: { icon: 'key-round', color: 'var(--slate-700)', label: 'Password Changed' },
    export_snapshot: { icon: 'download', color: 'var(--slate-700)', label: 'Export' },
    add_environment: { icon: 'server', color: 'var(--slate-700)', label: 'Environment Added' },
    remove_environment: { icon: 'server', color: 'var(--red-600)', label: 'Environment Removed' },
    switch_environment: { icon: 'server', color: 'var(--slate-700)', label: 'Environment Switch' },
  };
  // an action this console doesn't know yet shows as itself, never as some other action
  const actionMeta = (action) => ACTION_META[action] || { icon: 'activity', color: 'var(--slate-600)', label: action };
  const RESULT_TONE = { Success: 'success', Started: 'warning', Failed: 'danger' };
  const RESULT_ICON = { Success: { n: 'check-circle', c: 'var(--green-600)' }, Started: { n: 'clock', c: 'var(--amber-600)' }, Failed: { n: 'x-circle', c: 'var(--red-600)' } };

  function Dashboard({ nav, empty }) {
    const dlq = empty ? [] : D.queues.filter((q) => q.type !== 'NORMAL');
    // Every card is computed from the live data layer (queues + audit), nothing sampled.
    const total = dlq.reduce((sum, q) => sum + q.messages, 0);
    const largest = dlq.reduce((top, q) => (!top || q.messages > top.messages ? q : top), null);
    const today = new Date().toISOString().slice(0, 10); // audit times are UTC
    const failedToday = empty ? 0 : D.audit.filter((r) => r.result === 'Failed' && r.time.slice(0, 10) === today).length;
    const noConsumers = empty ? 0 : D.queues.filter((q) => q.consumers === 0).length;
    const autoRefresh = (((window.QL.serverSettings || {}).ui || {}).auto) !== false;
    const browseDepth = ((window.QL.serverSettings || {}).limits || {}).max_browse_depth
      || (window.QL.config || {}).max_browse_depth || 5000;
    return (
      <div>
        <PageHeader title="DLQ Recovery Dashboard" subtitle="Inspect failed RabbitMQ messages and recover them safely." />
        <Alert tone="info" style={{ marginBottom: 20 }}>
          Auto-refresh is {autoRefresh ? 'ON every 30 seconds' : 'OFF'}&nbsp;&nbsp;·&nbsp;&nbsp;Message bodies are read only when you open a queue (one scan, up to {browseDepth})&nbsp;&nbsp;·&nbsp;&nbsp;Counts from Management API
        </Alert>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(215px, 1fr))', gap: 14, marginBottom: 22 }}>
          <StatCard icon="database" tone="info" value={String(dlq.length)} label="DLQ Queues" sublabel="Detected" />
          <StatCard icon="inbox" tone="park" value={String(total)} label="Total Messages" sublabel="In DLQs" />
          <StatCard icon="bar-chart-3" tone="warning" value={largest && largest.messages ? String(largest.messages) : '—'} label="Largest DLQ" sublabel={largest && largest.messages ? largest.name : 'No DLQ messages'} />
          <StatCard icon="shield" tone="danger" value={String(failedToday)} label="Failed Actions" sublabel="Today" link={failedToday ? 'View failures' : undefined} onLinkClick={() => nav('audit')} />
          <StatCard icon="users" tone="success" value={String(noConsumers)} label="Queues with No" sublabel="Consumers" />
        </div>

        <Card title="Queues Needing Attention" subtitle="Sorted by message count (desc)"
          action={<ArrowLink onClick={() => nav('queues')}>View all queues</ArrowLink>} pad={false} style={{ marginBottom: 22 }}>
          {dlq.length === 0 ? (
            <EmptyState icon="check-circle" tone="success" title="All DLQs are clear"
              actions={<Button variant="secondary" onClick={() => nav('queues')}>Browse all queues</Button>}>
              No messages are waiting in any dead-letter queue. New failures will appear here automatically.
            </EmptyState>
          ) : (
          <DataTable rowKey="name" sortKey="messages"
            columns={[
              { key: 'name', label: 'Queue', render: (r) => <a href="#" onClick={(e) => { e.preventDefault(); nav('queuedetail', { queue: r.name }); }} style={{ color: 'var(--text-link)', fontWeight: 600, textDecoration: 'none' }}>{r.name}</a> },
              { key: 'type', label: 'Type', render: (r) => (
                <span style={{ display: 'inline-flex', gap: 6 }}>
                  <Badge tone={TYPE_TONE[r.type]}>{r.type}</Badge>
                  {r.retry && <Badge tone="warning" uppercase={false}>retry</Badge>}
                  {r.deliveryLimit != null && <span title={'Quorum queue with a delivery limit of ' + r.deliveryLimit + ': every preview counts as a delivery, so QueueLens will not browse it. Set the limit to -1 on RabbitMQ 4 (remove it on 3.x) to browse.'}><Badge tone="danger" uppercase={false}>not browsable</Badge></span>}
                </span>) },
              { key: 'messages', label: 'Messages', render: (r) => <span style={{ fontWeight: 600, color: r.status === 'attention' ? 'var(--red-600)' : 'var(--slate-700)' }}>{r.messages}</span> },
              { key: 'ready', label: 'Ready' },
              { key: 'consumers', label: 'Consumers' },
              { key: 'status', label: 'Status', render: (r) => { const s = STATUS[r.status]; return <StatusPill tone={s.tone} dot={s.dot}>{s.label}</StatusPill>; } },
              { key: 'last', label: 'Last Message', render: (r) => ({ '2m ago': '2 minutes ago', '5m ago': '5 minutes ago', '1m ago': '1 minute ago', '10m ago': '10 minutes ago' }[r.last] || r.last) },
              { key: 'a', label: 'Action', align: 'right', render: (r) => <Button size="sm" iconRight="chevron-right" onClick={() => nav('messages', { queue: r.name })}>Open</Button> },
            ]}
            rows={dlq} footer={`Showing ${dlq.length} of ${dlq.length} DLQ queues`} />
          )}
        </Card>

        <Card title="Recent Actions" subtitle="Latest operations performed in QueueLens"
          action={<ArrowLink onClick={() => nav('audit')}>View full audit log</ArrowLink>} pad={false} style={{ marginBottom: 22 }}>
          <DataTable rowKey="time"
            columns={[
              { key: 'st', label: '', width: 30, render: (r) => { const ic = RESULT_ICON[r.result]; return <Icon name={ic.n} size={17} color={ic.c} />; } },
              { key: 'time', label: 'Time' },
              { key: 'user', label: 'User', render: () => 'admin' },
              { key: 'action', label: 'Action', render: (r) => { const m = actionMeta(r.action); return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, color: m.color, fontWeight: 600 }}><Icon name={m.icon} size={14} />{m.label}</span>; } },
              { key: 'queue', label: 'Queue', render: (r) => <a href="#" onClick={(e) => e.preventDefault()} style={{ color: 'var(--text-link)', fontWeight: 600, textDecoration: 'none' }}>{r.queue}</a> },
              { key: 'target', label: 'Target' },
              { key: 'result', label: 'Result', render: (r) => <StatusPill tone={RESULT_TONE[r.result]}>{r.result}</StatusPill> },
            ]}
            rows={D.recentActions} />
        </Card>

        <Alert tone="success" title="QueueLens is operating in safe mode"
          action={<Button variant="success" size="sm" iconRight="external-link">Safety Documentation</Button>}>
          Browsing requeues messages&nbsp;&nbsp;·&nbsp;&nbsp;Replay publishes before ack&nbsp;&nbsp;·&nbsp;&nbsp;All actions are audited
        </Alert>
      </div>
    );
  }

  window.QL.screens.Dashboard = Dashboard;
  Object.assign(window.QL, { STATUS, TYPE_TONE, ACTION_META, actionMeta, RESULT_TONE, RESULT_ICON });
})();
