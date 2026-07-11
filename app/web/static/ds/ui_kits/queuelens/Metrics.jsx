// Metrics — the app's own Prometheus story: live queuelens_* values,
// the scrape config, and the bundled alert rules. Read-only, all roles.
(function () {
  const { Icon, Badge, CodeBlock } = window.__NS;
  const { PageHeader, Card } = window.QL;

  const SCRAPE_SNIPPET = [
    'scrape_configs:',
    '  - job_name: queuelens',
    '    metrics_path: /metrics',
    '    basic_auth:',
    '      username: <viewer-user>',
    '      password: <password>',
    '    static_configs:',
    "      - targets: ['queuelens.internal:8000']",
  ].join('\n');

  function Stat({ label, value, hint, tone }) {
    const color = tone === 'bad' ? 'var(--red-600)' : tone === 'good' ? 'var(--green-600)' : 'var(--slate-900)';
    return (
      <Card style={{ padding: '18px 20px', flex: 1, minWidth: 150 }}>
        <div style={{ fontSize: 26, fontWeight: 700, color, fontFamily: 'var(--font-mono)' }}>{value}</div>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--slate-700)', marginTop: 2 }}>{label}</div>
        {hint && <div style={{ fontSize: 12, color: 'var(--slate-400)' }}>{hint}</div>}
      </Card>
    );
  }

  function MiniTable({ head, rows, empty }) {
    if (!rows.length) return <div style={{ padding: '22px 0', textAlign: 'center', color: 'var(--slate-400)', fontSize: 13.5 }}>{empty}</div>;
    return (
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13.5 }}>
        <thead><tr>
          {head.map((h, i) => (
            <th key={h} style={{ textAlign: i === head.length - 1 ? 'right' : 'left', padding: '8px 10px', fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '.06em', color: 'var(--slate-400)', borderBottom: '1px solid var(--border-default)' }}>{h}</th>
          ))}
        </tr></thead>
        <tbody>
          {rows.map((cells, r) => (
            <tr key={r}>
              {cells.map((c, i) => (
                <td key={i} style={{ padding: '9px 10px', borderBottom: '1px solid var(--border-subtle, var(--border-default))', textAlign: i === cells.length - 1 ? 'right' : 'left', fontFamily: i === 0 ? 'var(--font-mono)' : undefined, color: i === 0 ? 'var(--slate-800)' : 'var(--slate-600)' }}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  function Metrics() {
    const [data, setData] = React.useState(null);
    const [rules, setRules] = React.useState('');
    const [error, setError] = React.useState(null);
    const getSync = (path) => {
      const x = new XMLHttpRequest();
      x.open('GET', path, false);
      x.send();
      if (x.status < 200 || x.status >= 300) throw new Error('HTTP ' + x.status);
      return x.responseText;
    };
    const load = () => {
      try {
        setData(JSON.parse(getSync('/api/metrics/summary')));
        setError(null);
      } catch (e) { setError(e.message); }
    };
    React.useEffect(() => {
      load();
      try { setRules(getSync('/api/metrics/alert-rules')); }
      catch (e) { /* rules panel just stays empty */ }
    }, []);

    if (error) {
      return (
        <div style={{ maxWidth: 1060, margin: '0 auto', padding: '28px 28px 60px' }}>
          <PageHeader title="Metrics" subtitle="Prometheus observability for this QueueLens instance." />
          <Card style={{ padding: 24, color: 'var(--red-600)', fontSize: 14 }}>Could not load metrics: {error}</Card>
        </div>
      );
    }
    if (!data) return null;

    return (
      <div style={{ maxWidth: 1060, margin: '0 auto', padding: '28px 28px 60px' }}>
        <PageHeader
          title="Metrics"
          subtitle="What this instance exports at /metrics — live values, the scrape config, and the bundled Prometheus alert rules."
          actions={<button className="ql-btn" onClick={load}><Icon name="refresh-cw" size={14} /> Refresh</button>}
        />

        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 22 }}>
          <Stat label="Broker" value={data.rabbitmq_ready ? 'UP' : 'DOWN'} tone={data.rabbitmq_ready ? 'good' : 'bad'} hint="queuelens_rabbitmq_ready" />
          <Stat label="DLQ backlog" value={data.dlq_backlog} tone={data.dlq_backlog > 0 ? 'bad' : 'good'} hint="sum of queuelens_dlq_messages" />
          <Stat label="Previews served" value={data.preview_requests} hint="queuelens_preview_requests_total" />
          <Stat label="Actions OK" value={data.actions_succeeded} tone="good" hint='queuelens_actions_total{result="success"}' />
          <Stat label="Actions failed" value={data.actions_failed} tone={data.actions_failed > 0 ? 'bad' : undefined} hint='queuelens_actions_total{result="failed"}' />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 18, marginBottom: 22 }}>
          <Card style={{ padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <Icon name="inbox" size={16} color="var(--red-600)" />
              <b style={{ fontSize: 14.5 }}>Dead-letter backlog</b>
              <Badge tone="neutral">{data.dlq.length} queues</Badge>
            </div>
            <MiniTable head={['queue', 'messages']} empty="No dead-letter queues detected."
              rows={data.dlq.map((r) => [r.queue, r.messages])} />
          </Card>
          <Card style={{ padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <Icon name="zap" size={16} color="var(--blue-600)" />
              <b style={{ fontSize: 14.5 }}>Actions since start</b>
            </div>
            <MiniTable head={['action', 'result', 'count']} empty="No actions executed since this instance started."
              rows={data.actions.map((r) => [r.action, r.result, r.count])} />
            {data.operations.length > 0 && (
              <div style={{ marginTop: 14 }}>
                <div style={{ fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '.06em', color: 'var(--slate-400)', marginBottom: 4 }}>Broker operation latency (avg)</div>
                <MiniTable head={['action', 'calls', 'avg']} empty=""
                  rows={data.operations.map((r) => [r.action, r.count, (r.avg_seconds * 1000).toFixed(0) + ' ms'])} />
              </div>
            )}
          </Card>
        </div>

        <Card style={{ padding: 20, marginBottom: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <Icon name="radar" size={16} color="var(--purple-600)" />
            <b style={{ fontSize: 14.5 }}>Scrape this instance</b>
          </div>
          <p style={{ fontSize: 13.5, color: 'var(--slate-500)', margin: '0 0 12px' }}>
            <code style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5 }}>/metrics</code> serves Prometheus
            text format behind the same Basic auth as the app — a read-only Viewer account is enough.
          </p>
          <CodeBlock code={SCRAPE_SNIPPET} copy maxHeight={220} />
        </Card>

        <Card style={{ padding: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <Icon name="bell-ring" size={16} color="var(--amber-600, #d97706)" />
            <b style={{ fontSize: 14.5 }}>Bundled alert rules</b>
          </div>
          <p style={{ fontSize: 13.5, color: 'var(--slate-500)', margin: '0 0 12px' }}>
            Ready-made Alertmanager rules for broker loss, DLQ depth, DLQ growth, and action failures.
            Copy them into your Prometheus rules directory and tune the thresholds to your traffic.
          </p>
          {rules
            ? <CodeBlock code={rules} copy maxHeight={380} />
            : <div style={{ padding: '18px 0', color: 'var(--slate-400)', fontSize: 13.5 }}>Rules file not available on this instance.</div>}
        </Card>
      </div>
    );
  }

  window.QL.screens.Metrics = Metrics;
})();
