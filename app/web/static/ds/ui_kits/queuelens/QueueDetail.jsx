// Queue detail page — live stats, x-death breakdown from real messages, bindings, facts.
(function () {
  const { Icon, Badge, StatusPill, Button, Alert, KeyValue } = window.__NS;
  const { PageHeader, Card, Breadcrumbs, ArrowLink } = window.QL;

  const REASON_COLOR = {
    rejected: 'var(--red-600)', expired: 'var(--amber-600)',
    maxlen: 'var(--purple-600)', delivery_limit: 'var(--red-600)',
  };

  function QueueDetail({ nav, queue }) {
    const info = React.useMemo(() => window.QL.fetchQueueInfo(queue), [queue]);
    const messages = React.useMemo(() => window.QL.fetchMessages(queue), [queue]);
    const topo = React.useMemo(() => window.QL.fetchTopology(), []);

    if (!info) {
      const { EmptyState } = window.QL;
      return (
        <div>
          <Breadcrumbs items={[{ label: 'Queues', onClick: () => nav('queues') }, { label: queue }]} />
          <div style={{ padding: '60px 0' }}>
            <EmptyState icon="search-x" tone="danger" title="Queue not found"
              actions={<Button variant="secondary" onClick={() => nav('queues')}>Back to Queues</Button>}>
              {queue} does not exist in this vhost — it may have been deleted.
            </EmptyState>
          </div>
        </div>
      );
    }

    const isDlq = info.is_dlq;
    const row = window.QL.data.queues.find((q) => q.name === queue) || {};
    // Why messages landed here — counted from the latest x-death entry of each previewed message.
    const reasons = {};
    messages.forEach((m) => {
      const d = (m.xdeathRaw || [])[0];
      if (d && d.reason) reasons[d.reason] = (reasons[d.reason] || 0) + 1;
    });
    const breakdown = Object.entries(reasons).map(([reason, count]) => ({
      reason, count, pct: Math.round((count / messages.length) * 100),
    })).sort((a, b) => b.count - a.count);

    const inBindings = topo.bindings.filter((b) => b.destination === queue && b.destination_type === 'queue');
    const topoQueue = topo.queues.find((q) => q.name === queue) || {};

    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Breadcrumbs items={[{ label: 'Queues', onClick: () => nav('queues') }, { label: queue }]} />
          <PageHeader title={queue}
            after={<span style={{ display: 'inline-flex', gap: 6 }}>
              {isDlq && <Badge tone="danger">DLQ</Badge>}
              {row.qtype && row.qtype !== 'classic' && <Badge tone={row.qtype === 'quorum' ? 'park' : 'info'} uppercase={false}>{row.qtype}</Badge>}
              <StatusPill tone={row.status === 'attention' ? 'danger' : row.status === 'active' ? 'success' : 'info'} dot>
                {row.status === 'attention' ? 'Needs Attention' : row.status === 'active' ? 'Active' : 'Idle'}
              </StatusPill>
            </span>}
            subtitle="Real-time view of this queue from the Management API."
            actions={<Button variant="secondary" onClick={() => nav('messages', { queue })} style={{ color: 'var(--text-link)' }}>Browse Messages</Button>} />

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 14, marginBottom: 18 }}>
            {[['Messages', info.messages], ['Ready', info.messages_ready], ['Unacked', info.messages_unacked], ['Consumers', info.consumers]].map(([label, value]) => (
              <Card key={label}>
                <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>{label}</div>
                <div style={{ fontSize: 26, fontWeight: 700, color: label === 'Consumers' && value === 0 && info.messages > 0 ? 'var(--amber-600)' : 'var(--text-heading)', marginTop: 4 }}>{value}</div>
              </Card>
            ))}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18, marginBottom: 18 }}>
            <Card title={`x-death Breakdown (${messages.length} previewed)`} subtitle="Why messages landed here — from each message's latest x-death entry.">
              {breakdown.length === 0 ? (
                <div style={{ fontSize: 13, color: 'var(--text-muted)', padding: '8px 0' }}>
                  {messages.length === 0 ? 'Queue is empty.' : 'No dead-letter history — messages were published here directly.'}
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 6 }}>
                  {breakdown.map((x) => (
                    <div key={x.reason}>
                      <div style={{ display: 'flex', fontSize: 13, marginBottom: 5 }}>
                        <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--slate-700)', fontWeight: 600 }}>{x.reason}</span>
                        <span style={{ marginLeft: 'auto', color: 'var(--slate-500)' }}>{x.count} · {x.pct}%</span>
                      </div>
                      <div style={{ height: 7, borderRadius: 999, background: 'var(--slate-100)', overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: x.pct + '%', borderRadius: 999, background: REASON_COLOR[x.reason] || 'var(--slate-400)' }}></div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card title="Bindings" subtitle="How messages arrive, and where this queue dead-letters.">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 12.5 }}>
                {inBindings.length === 0 && (
                  <div style={{ fontFamily: 'var(--font-ui)', fontSize: 13, color: 'var(--text-muted)' }}>
                    No exchange bindings — reachable via the default exchange only.
                  </div>
                )}
                {inBindings.map((b, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', background: 'var(--slate-50)', border: '1px solid var(--border-default)', borderRadius: 8, flexWrap: 'wrap' }}>
                    <span style={{ color: 'var(--slate-700)' }}>{b.source || '(default)'}</span>
                    <Icon name="arrow-right" size={13} color="var(--slate-400)" />
                    <span style={{ color: 'var(--slate-500)' }}>{b.routing_key || '—'}</span>
                    <Icon name="arrow-right" size={13} color="var(--slate-400)" />
                    <span style={{ color: 'var(--blue-600)', fontWeight: 600 }}>{queue}</span>
                  </div>
                ))}
                {topoQueue.dlx != null && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', background: 'var(--red-50)', border: '1px dashed var(--red-200)', borderRadius: 8, flexWrap: 'wrap' }}>
                    <span style={{ color: 'var(--slate-700)' }}>x-dead-letter-exchange</span>
                    <Icon name="arrow-right" size={13} color="var(--red-600)" />
                    <span style={{ color: 'var(--red-600)', fontWeight: 600 }}>{topoQueue.dlx || '(default)'}{topoQueue.dlx_routing_key ? ' / ' + topoQueue.dlx_routing_key : ''}</span>
                  </div>
                )}
              </div>
              <ArrowLink onClick={() => nav('topology')}>View full topology</ArrowLink>
            </Card>
          </div>
        </div>

        <aside style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none', display: 'flex', flexDirection: 'column', gap: 16, position: 'sticky', top: 16 }}>
          <Card title="Queue Facts">
            <KeyValue gap={12} items={[
              { label: 'VHost', value: info.vhost || '/', mono: true },
              { label: 'Durability', value: info.durable ? 'durable' : 'transient' },
              { label: 'Queue Type', value: row.qtype || 'classic', mono: true },
              { label: 'Kind', value: info.kind || (isDlq ? 'dlq' : 'normal') },
              { label: 'Publish Rate', value: row.rate != null ? row.rate + '/s' : '—' },
              { label: 'Idle Since', value: row.last || '—' },
            ]} />
          </Card>
          {info.consumers === 0 && info.messages > 0 && (
            <Alert tone="warning" title={`0 consumers on a queue holding ${info.messages} messages.`}>
              Attach a consumer or replay the backlog — nothing is draining this queue.
            </Alert>
          )}
        </aside>
      </div>
    );
  }

  window.QL.screens.QueueDetail = QueueDetail;
})();
