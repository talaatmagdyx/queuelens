// Alerts screen — read-only view of the packaged Prometheus alert rules.
// QueueLens delegates alerting to Prometheus/Alertmanager (see docs/OPERATIONS.md):
// /metrics is scraped, and these rules fire through your existing channels.
(function () {
  const { StatusPill, DataTable, Alert, CodeBlock } = window.__NS;
  const { PageHeader, Card, EmptyState } = window.QL;

  const SEV_TONE = { critical: 'danger', warning: 'warning', info: 'info' };

  function Alerts() {
    const data = React.useMemo(() => window.QL.fetchAlertRules(), []);
    const [sel, setSel] = React.useState(data.rules[0] || null);

    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageHeader title="Alerts" subtitle="Prometheus alert rules shipped with QueueLens. Scrape /metrics and route these through Alertmanager." />
          {data.rules.length === 0 ? (
            <div style={{ padding: '40px 0' }}>
              <EmptyState icon="bell-ring" tone="neutral" title="No packaged alert rules found">
                Add rules under deploy/prometheus/alerts.yml and they will appear here.
              </EmptyState>
            </div>
          ) : (
            <Card pad={false}>
              <DataTable rowKey="name" onRowClick={setSel} selectedKey={sel && sel.name}
                columns={[
                  { key: 'name', label: 'Rule', render: (r) => <span style={{ fontWeight: 600, color: 'var(--slate-900)' }}>{r.name}</span> },
                  { key: 'expr', label: 'Condition', render: (r) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--slate-600)', whiteSpace: 'normal', display: 'inline-block', maxWidth: 320 }}>{r.expr}</span> },
                  { key: 'for', label: 'For', render: (r) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{r.for}</span> },
                  { key: 'severity', label: 'Severity', render: (r) => <StatusPill tone={SEV_TONE[r.severity] || 'info'}>{r.severity}</StatusPill> },
                ]}
                rows={data.rules} footer={`${data.rules.length} rules · ${data.source || ''} — edit the file and redeploy to change them`} />
            </Card>
          )}
          <Alert tone="info" style={{ marginTop: 18 }}>
            QueueLens exposes <span style={{ fontFamily: 'var(--font-mono)' }}>/metrics</span> (Prometheus format) — DLQ depth per queue,
            action outcomes, broker connectivity. Alert delivery (Slack, PagerDuty, email) is Alertmanager's job, so it works with
            the channels you already have.
          </Alert>
        </div>

        {sel && (
          <aside style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none', display: 'flex', flexDirection: 'column', gap: 16, position: 'sticky', top: 16 }}>
            <Card title={sel.name}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-heading)', marginBottom: 6 }}>Summary</div>
              <div style={{ fontSize: 13, color: 'var(--text-body)', lineHeight: 1.6 }}>{sel.summary || '—'}</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-heading)', margin: '14px 0 6px' }}>Description</div>
              <div style={{ fontSize: 13, color: 'var(--text-body)', lineHeight: 1.6 }}>{sel.description || '—'}</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-heading)', margin: '14px 0 6px' }}>PromQL</div>
              <CodeBlock code={`${sel.expr}\nfor: ${sel.for}`} />
            </Card>
          </aside>
        )}
      </div>
    );
  }

  window.QL.screens.Alerts = Alerts;
})();
