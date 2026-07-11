// Metrics — the app's own Prometheus story: live queuelens_* values,
// the scrape config, and the bundled alert rules. Read-only, all roles.
(function () {
  const { Icon, Badge, StatCard, CodeBlock, Tabs, Button } = window.__NS;
  const { PageHeader, Card, EmptyState } = window.QL;

  const SCRAPE_SNIPPET = [
    'scrape_configs:',
    '  - job_name: queuelens',
    '    metrics_path: /metrics',
    '    basic_auth:',
    '      username: <viewer-user>   # a read-only Viewer account is enough',
    '      password: <password>',
    '    static_configs:',
    "      - targets: ['queuelens.internal:8000']",
  ].join('\n');

  // queue name, thin proportional bar, count — reads like a chart, scans like a table
  function BacklogRow({ name, value, max }) {
    const pct = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
    const hot = value > 0;
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '7px 0' }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--slate-700)', width: 220, flex: 'none', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
        <span style={{ flex: 1, height: 8, borderRadius: 99, background: 'var(--slate-100)', overflow: 'hidden' }}>
          <span style={{ display: 'block', height: '100%', width: pct + '%', borderRadius: 99, background: hot ? 'var(--red-400, #f87171)' : 'var(--slate-200)', transition: 'width .4s' }} />
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, fontWeight: 700, color: hot ? 'var(--red-600)' : 'var(--slate-400)', width: 52, textAlign: 'right', flex: 'none' }}>{value}</span>
      </div>
    );
  }

  function ResultPill({ result }) {
    const ok = result === 'success';
    return (
      <span style={{ fontSize: 11.5, fontWeight: 600, padding: '2px 9px', borderRadius: 99, background: ok ? 'var(--green-100)' : 'var(--red-100)', color: ok ? 'var(--green-600)' : 'var(--red-600)' }}>
        {result}
      </span>
    );
  }

  function Metrics() {
    const [data, setData] = React.useState(null);
    const [rules, setRules] = React.useState('');
    const [tab, setTab] = React.useState('scrape');
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

    const wrap = (children) => (
      <div style={{ maxWidth: 1060, margin: '0 auto', padding: '28px 28px 60px' }}>
        <PageHeader
          title="Metrics"
          subtitle="What this instance exports at /metrics — live values, the scrape config, and the bundled Prometheus alert rules."
          actions={<Button variant="secondary" icon="refresh-cw" onClick={load}>Refresh</Button>}
        />
        {children}
      </div>
    );

    if (error) return wrap(<Card><EmptyState icon="alert-triangle" tone="danger" title="Could not load metrics">{error}</EmptyState></Card>);
    if (!data) return null;

    const maxDlq = Math.max(1, ...data.dlq.map((r) => r.messages));
    const ruleCount = (rules.match(/- alert:/g) || []).length;

    return wrap(
      <React.Fragment>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 14, marginBottom: 20 }}>
          <StatCard icon="activity" tone={data.rabbitmq_ready ? 'success' : 'danger'} value={data.rabbitmq_ready ? 'UP' : 'DOWN'} label="Broker" sublabel="AMQP link" />
          <StatCard icon="inbox" tone={data.dlq_backlog > 0 ? 'danger' : 'success'} value={String(data.dlq_backlog)} label="DLQ backlog" sublabel={data.dlq.length + ' queues'} />
          <StatCard icon="eye" tone="info" value={String(data.preview_requests)} label="Previews" sublabel="Reads only" />
          <StatCard icon="check-circle" tone="success" value={String(data.actions_succeeded)} label="Actions OK" sublabel="Replay · park" />
          <StatCard icon="x-circle" tone={data.actions_failed > 0 ? 'danger' : 'park'} value={String(data.actions_failed)} label="Failed" sublabel="See audit log" />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 18, marginBottom: 20, alignItems: 'start' }}>
          <Card title="Dead-letter backlog" subtitle="queuelens_dlq_messages — refreshed on every load"
            action={<Badge tone="neutral">{data.dlq.length} queues</Badge>}>
            {data.dlq.length === 0
              ? <EmptyState icon="check-circle" tone="success" title="No dead-letter queues detected">Nothing is dead-lettering right now.</EmptyState>
              : data.dlq.map((r) => <BacklogRow key={r.queue} name={r.queue} value={r.messages} max={maxDlq} />)}
          </Card>

          <div style={{ display: 'grid', gap: 18 }}>
            <Card title="Actions since start" subtitle='queuelens_actions_total by action and result'>
              {data.actions.length === 0
                ? <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0 4px', color: 'var(--slate-400)', fontSize: 13.5 }}>
                    <span style={{ width: 34, height: 34, borderRadius: 999, background: 'var(--slate-100)', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}><Icon name="zap" size={16} color="var(--slate-400)" /></span>
                    Counters reset with the process — run a replay or park and it shows up here.
                  </div>
                : data.actions.map((r, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 0', borderBottom: i < data.actions.length - 1 ? '1px solid var(--slate-100)' : 'none' }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--slate-700)', flex: 1 }}>{r.action}</span>
                      <ResultPill result={r.result} />
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, fontWeight: 700, color: 'var(--slate-700)', width: 44, textAlign: 'right' }}>{r.count}</span>
                    </div>
                  ))}
            </Card>
            <Card title="Broker operation latency" subtitle="Average per action, this process">
              {data.operations.length === 0
                ? <div style={{ color: 'var(--slate-400)', fontSize: 13.5, padding: '4px 0' }}>No timed operations yet.</div>
                : data.operations.map((r) => (
                    <div key={r.action} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0' }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--slate-700)', flex: 1 }}>{r.action}</span>
                      <span style={{ fontSize: 12, color: 'var(--slate-400)' }}>{r.count} calls</span>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, fontWeight: 700, color: 'var(--slate-700)', width: 70, textAlign: 'right' }}>{(r.avg_seconds * 1000).toFixed(0)} ms</span>
                    </div>
                  ))}
            </Card>
          </div>
        </div>

        <Card title="Prometheus integration" subtitle="Point your Prometheus at this instance, then load the bundled rules and tune the thresholds to your traffic." pad={false}>
          <div style={{ padding: '0 20px' }}>
            <Tabs active={tab} onChange={setTab} tabs={[
              { id: 'scrape', label: 'Scrape config', icon: 'radar' },
              { id: 'rules', label: 'Alert rules', icon: 'bell-ring', count: ruleCount || undefined },
            ]} />
          </div>
          <div style={{ padding: '16px 20px 18px' }}>
            {tab === 'scrape'
              ? <CodeBlock code={SCRAPE_SNIPPET} copy />
              : rules
                ? <CodeBlock code={rules} copy maxHeight={360} />
                : <div style={{ color: 'var(--slate-400)', fontSize: 13.5 }}>Rules file not available on this instance.</div>}
          </div>
        </Card>
      </React.Fragment>
    );
  }

  window.QL.screens.Metrics = Metrics;
})();
