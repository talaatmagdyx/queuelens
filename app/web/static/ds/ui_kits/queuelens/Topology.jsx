// Topology view — live exchanges → bindings → queues, dead-letter flows highlighted.
(function () {
  const { Icon, Alert } = window.__NS;
  const { PageHeader, Card, EmptyState } = window.QL;

  function Node({ children, tone = 'neutral', icon }) {
    const tones = {
      neutral: { bg: 'var(--slate-50)', border: 'var(--border-default)', color: 'var(--slate-700)' },
      exchange: { bg: 'var(--blue-50)', border: 'var(--blue-200)', color: 'var(--blue-700)' },
      danger: { bg: 'var(--red-50)', border: 'var(--red-200)', color: 'var(--red-600)' },
      park: { bg: 'var(--purple-50)', border: 'var(--purple-100)', color: 'var(--purple-600)' },
    };
    const t = tones[tone];
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 8, background: t.bg, border: `1px solid ${t.border}`, color: t.color, fontFamily: 'var(--font-mono)', fontSize: 12.5, fontWeight: 600, whiteSpace: 'nowrap' }}>
        {icon && <Icon name={icon} size={13} />}{children}
      </span>
    );
  }

  function Arrow({ label, tone = 'neutral' }) {
    const color = tone === 'danger' ? 'var(--red-600)' : 'var(--slate-400)';
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flex: 'none' }}>
        <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: tone === 'danger' ? 'var(--red-600)' : 'var(--slate-500)' }}>{label}</span>
        <span style={{ width: 34, height: 0, borderTop: `2px ${tone === 'danger' ? 'dashed' : 'solid'} ${color}`, position: 'relative' }}>
          <Icon name="chevron-right" size={12} color={color} style={{ position: 'absolute', right: -8, top: -7 }} />
        </span>
      </span>
    );
  }

  function queueTone(name) {
    if (name.endsWith('.parking') || name.startsWith('parking.')) return 'park';
    if (name.includes('dlq')) return 'danger';
    return 'neutral';
  }

  function Topology({ nav }) {
    const topo = React.useMemo(() => window.QL.fetchTopology(), []);
    const queueByName = {};
    topo.queues.forEach((q) => { queueByName[q.name] = q; });

    // One flow per exchange that routes to at least one queue.
    const flows = topo.exchanges.map((e) => ({
      exchange: e.name, type: e.type,
      routes: topo.bindings
        .filter((b) => b.source === e.name && b.destination_type === 'queue')
        .map((b) => {
          const q = queueByName[b.destination] || {};
          // Resolve the dead-letter hop: a queue bound to this queue's DLX
          // with a matching routing key, falling back to the exchange name.
          let dlqTarget = null;
          if (q.dlx != null) {
            const key = q.dlx_routing_key || q.name;
            const hit = topo.bindings.find((x) => x.source === q.dlx && x.destination_type === 'queue' && x.routing_key === key);
            dlqTarget = hit ? hit.destination : (q.dlx || '(default)') + ' / ' + key;
          }
          return { rk: b.routing_key || '—', queue: b.destination, consumers: q.consumers || 0, dlq: dlqTarget };
        }),
    })).filter((f) => f.routes.length > 0);

    // Queues only reachable via the default exchange, but with a DLX configured —
    // their dead-letter flow is still worth showing.
    const bound = new Set(topo.bindings.filter((b) => b.destination_type === 'queue' && b.source).map((b) => b.destination));
    const defaultOnly = topo.queues.filter((q) => !bound.has(q.name) && q.dlx != null).map((q) => {
      const key = q.dlx_routing_key || q.name;
      const hit = topo.bindings.find((x) => x.source === q.dlx && x.destination_type === 'queue' && x.routing_key === key);
      return { queue: q.name, consumers: q.consumers || 0, dlq: hit ? hit.destination : (q.dlx || '(default)') + ' / ' + key };
    });

    return (
      <div>
        <PageHeader title="Topology" subtitle="Exchanges, bindings, and queues — with dead-letter flows highlighted. Live from the Management API." />
        {flows.length === 0 && defaultOnly.length === 0 ? (
          <div style={{ padding: '40px 0' }}>
            <EmptyState icon="git-fork" tone="neutral" title="No exchange bindings">
              Every queue in this vhost is reached via the default exchange, and none declares a dead-letter exchange.
            </EmptyState>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {flows.map((f) => (
              <Card key={f.exchange} pad={false}>
                <div style={{ padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {f.routes.map((r, i) => (
                    <div key={r.queue + i} style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                      <span style={{ width: 230, flex: 'none' }}>
                        {i === 0 ? <Node tone="exchange" icon="shuffle">{f.exchange} · {f.type}</Node> : null}
                      </span>
                      <Arrow label={r.rk} />
                      <a href="#" onClick={(e) => { e.preventDefault(); nav('queuedetail', { queue: r.queue }); }} style={{ textDecoration: 'none' }}>
                        <Node tone={queueTone(r.queue)} icon={queueTone(r.queue) === 'park' ? 'flag' : queueTone(r.queue) === 'danger' ? 'alert-triangle' : 'list'}>{r.queue}</Node>
                      </a>
                      <span style={{ fontSize: 12, color: r.consumers === 0 ? 'var(--amber-600)' : 'var(--slate-500)', fontWeight: 600 }}>
                        {r.consumers} consumer{r.consumers === 1 ? '' : 's'}
                      </span>
                      {r.dlq && (
                        <React.Fragment>
                          <Arrow label="on failure" tone="danger" />
                          <a href="#" onClick={(e) => { e.preventDefault(); if (queueByName[r.dlq]) nav('queuedetail', { queue: r.dlq }); }} style={{ textDecoration: 'none' }}>
                            <Node tone="danger" icon="alert-triangle">{r.dlq}</Node>
                          </a>
                        </React.Fragment>
                      )}
                    </div>
                  ))}
                </div>
              </Card>
            ))}
            {defaultOnly.length > 0 && (
              <Card pad={false}>
                <div style={{ padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {defaultOnly.map((r, i) => (
                    <div key={r.queue} style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                      <span style={{ width: 230, flex: 'none' }}>
                        {i === 0 ? <Node tone="exchange" icon="shuffle">(default exchange)</Node> : null}
                      </span>
                      <Arrow label={r.queue} />
                      <a href="#" onClick={(e) => { e.preventDefault(); nav('queuedetail', { queue: r.queue }); }} style={{ textDecoration: 'none' }}>
                        <Node tone={queueTone(r.queue)} icon={queueTone(r.queue) === 'park' ? 'flag' : queueTone(r.queue) === 'danger' ? 'alert-triangle' : 'list'}>{r.queue}</Node>
                      </a>
                      <span style={{ fontSize: 12, color: r.consumers === 0 ? 'var(--amber-600)' : 'var(--slate-500)', fontWeight: 600 }}>
                        {r.consumers} consumer{r.consumers === 1 ? '' : 's'}
                      </span>
                      <Arrow label="on failure" tone="danger" />
                      <a href="#" onClick={(e) => { e.preventDefault(); if (queueByName[r.dlq]) nav('queuedetail', { queue: r.dlq }); }} style={{ textDecoration: 'none' }}>
                        <Node tone="danger" icon="alert-triangle">{r.dlq}</Node>
                      </a>
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </div>
        )}
        <Alert tone="info" style={{ marginTop: 18 }}>
          Red dashed arrows are <span style={{ fontFamily: 'var(--font-mono)' }}>x-dead-letter-exchange</span> routes. Purple nodes are parking queues managed by QueueLens.
        </Alert>
      </div>
    );
  }

  window.QL.screens.Topology = Topology;
})();
