// Configuration screen with Connection Status side panel — full design version.
// Live values (broker, limits, audit size) come from the API; UI-level settings
// persist in localStorage. Connection is env-var driven, so edits here are local.
(function () {
  const { Icon, Badge, StatusPill, Button, Alert, Tabs, Select, Input, Switch } = window.__NS;
  const { PageHeader, Card } = window.QL;
  const D = window.QL.data;
  const B = window.QL.broker || {};

  const CFG = window.QL.fetchConfig();

  function loadToggles() {
    try {
      const stored = localStorage.getItem('ql_settings');
      if (stored) return JSON.parse(stored);
    } catch (e) {}
    return { tls: false, verify: false, auto: true, limits: true, confirm: true, confirmDanger: true, typeConfirm: true, preferPark: true, hReplayed: true, hSource: true, hAt: true, hAction: true, hUser: true, auditReads: false, syslog: false };
  }

  function SideRow({ icon, label, value }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 0', fontSize: 13.5 }}>
        <Icon name={icon} size={16} color="var(--slate-400)" />
        <span style={{ color: 'var(--slate-600)' }}>{label}</span>
        <span style={{ marginLeft: 'auto', fontWeight: 600, color: 'var(--slate-900)', textAlign: 'right' }}>{value}</span>
      </div>
    );
  }

  function CheckRow({ label, right }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0', fontSize: 13.5 }}>
        <Icon name="check" size={14} color="var(--green-600)" strokeWidth={2.5} />
        <span style={{ color: 'var(--slate-600)' }}>{label}</span>
        <span style={{ marginLeft: 'auto', fontWeight: 600, color: 'var(--green-600)' }}>{right}</span>
      </div>
    );
  }

  function DangerRow({ title, desc, btn, onClick }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '14px 16px', background: 'var(--surface-card)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', marginTop: 10 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--slate-900)' }}>{title}</div>
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 2 }}>{desc}</div>
        </div>
        <Button variant="danger" size="sm" onClick={onClick}>{btn}</Button>
      </div>
    );
  }

  function EnvRow({ p, active, vhost, onEnvChange }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '14px 16px', background: active ? 'var(--blue-50)' : 'var(--surface-card)', border: `1px solid ${active ? 'var(--blue-200)' : 'var(--border-default)'}`, borderRadius: 'var(--radius-md)' }}>
        <StatusPill tone={p.id === 'production' ? 'danger' : 'info'} dot size="sm" style={{ textTransform: 'uppercase', letterSpacing: '0.04em', flex: 'none' }}>{p.label}</StatusPill>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--slate-900)', fontFamily: 'var(--font-mono)' }}>{p.api}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>vhosts:</span>
            {p.vhosts.map((v) => (
              <span key={v.id} style={{ padding: '2px 8px', borderRadius: 6, background: active && v.id === vhost ? 'var(--blue-100)' : 'var(--slate-100)', color: active && v.id === vhost ? 'var(--blue-700)' : 'var(--slate-600)', fontSize: 12, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{v.id}</span>
            ))}
          </div>
        </div>
        {active
          ? <StatusPill tone="success">Active</StatusPill>
          : <Button variant="secondary" size="sm" onClick={() => onEnvChange && onEnvChange(p.id)}>Set Active</Button>}
      </div>
    );
  }

  function ToggleRow({ label, desc, checked, onChange, locked }) {
    return (
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, padding: '14px 16px', background: 'var(--surface-card)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)' }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600, color: 'var(--slate-900)' }}>
            {label}
            {locked && <Badge tone="success" uppercase={false}>always on</Badge>}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 2 }}>{desc}</div>
        </div>
        <Switch checked={locked ? true : checked} onChange={locked ? undefined : onChange} />
      </div>
    );
  }

  function SafetyTab({ toggles, t }) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <Card title="Safety Defaults" subtitle="Core safety guarantees. Locked defaults cannot be disabled.">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
            <ToggleRow locked label="Publish-before-ack" desc="A message is only acknowledged after the broker confirms the publish to its destination." />
            <ToggleRow locked label="Non-destructive browsing" desc="Browsing fetches messages with requeue — reading never removes anything." />
            <ToggleRow locked label="Audit everything" desc="Every replay, park, and delete is recorded with user, environment, and vhost." />
            <ToggleRow locked label="Confirm dangerous actions" desc="Every replay, park, delete, and bulk execute requires an explicit confirmation." />
            <ToggleRow label="Type-to-confirm in production" desc="Require typing the queue name before any action in production environments." checked={toggles.typeConfirm} onChange={t('typeConfirm')} />
            <ToggleRow label="Prefer Park over Delete" desc="Suggest parking when a delete is requested on messages with x-death ≥ 3." checked={toggles.preferPark} onChange={t('preferPark')} />
          </div>
          <Alert tone="success" style={{ marginTop: 14 }}>Messages never lost: with these defaults, no QueueLens operation can drop a message.</Alert>
        </Card>
      </div>
    );
  }

  function LimitsTab() {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <Card title="Limits" subtitle="Caps that keep browsing and bulk operations safe on large queues.">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 8 }}>
            <Input label="Message Preview Limit" defaultValue={String(CFG.max_preview_messages ?? 100)} suffix="msgs" />
            <Input label="Refetch Window" defaultValue={String(CFG.refetch_window_size ?? 100)} suffix="msgs" />
            <Input label="Max Bulk Selection" defaultValue={String(CFG.max_bulk_size ?? 500)} suffix="msgs" />
          </div>
          <Alert tone="info" style={{ marginTop: 14 }}>Preview limit caps how many messages are fetched (with requeue) per queue. Larger values increase broker load.</Alert>
        </Card>
        <Card title="Timeouts" subtitle="How long QueueLens waits on the Management API and publishes.">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 8 }}>
            <Input label="Operation Timeout" defaultValue={String(CFG.operation_timeout_seconds ?? 10)} suffix="sec" />
            <Input label="Bulk Dry-Run TTL" defaultValue={String(CFG.bulk_dry_run_ttl_seconds ?? 600)} suffix="sec" />
            <Input label="Max Message Size" defaultValue={CFG.max_message_size_bytes ? String(Math.round(CFG.max_message_size_bytes / 1024)) : '1024'} suffix="KB" />
          </div>
        </Card>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', alignItems: 'center' }}>
          <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>Values load from QUEUELENS_* environment variables — change them there and restart to apply.</span>
          <Button variant="secondary" onClick={() => location.reload()}>Reset to Current</Button>
        </div>
      </div>
    );
  }

  function HeaderToggleRow({ k, v, checked, onChange, locked }) {
    return (
      <tr>
        <td style={{ padding: '10px 14px', fontSize: 13, fontFamily: 'var(--font-mono)', color: 'var(--slate-600)', borderTop: '1px solid var(--slate-100)' }}>{k}</td>
        <td style={{ padding: '10px 14px', fontSize: 13, fontFamily: 'var(--font-mono)', color: 'var(--slate-700)', borderTop: '1px solid var(--slate-100)' }}>{v}</td>
        <td style={{ padding: '10px 14px', textAlign: 'right', borderTop: '1px solid var(--slate-100)' }}>
          {locked ? <Badge tone="success" uppercase={false}>always added</Badge> : <Switch checked={checked} onChange={onChange} />}
        </td>
      </tr>
    );
  }

  function HeadersTab() {
    const th = { textAlign: 'left', padding: '8px 14px', fontSize: 12.5, fontWeight: 600, color: 'var(--slate-500)', background: 'var(--surface-table-header)' };
    const nowIso = new Date().toISOString();
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <Card title="Replay Headers" subtitle="Headers QueueLens adds so replayed messages are traceable downstream." pad={false}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={th}>Header</th><th style={th}>Example Value</th><th style={{ ...th, textAlign: 'right' }}>Enabled</th></tr></thead>
            <tbody>
              <HeaderToggleRow locked k="x-queuelens-replayed" v="true" />
              <HeaderToggleRow locked k="x-queuelens-source-queue" v={window.QL.defaultQueue || 'orders.dlq'} />
              <HeaderToggleRow locked k="x-queuelens-replayed-at" v={nowIso} />
              <HeaderToggleRow locked k="x-queuelens-action" v="replay_move" />
              <HeaderToggleRow locked k="x-queuelens-replayed-by" v={window.QL.user || 'admin'} />
              <HeaderToggleRow locked k="x-queuelens-original-fingerprint" v="c1f7c3d261f5cca2…" />
            </tbody>
          </table>
        </Card>
        <Card title="Custom Headers" subtitle="Added to every message QueueLens publishes.">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 12, alignItems: 'end', marginTop: 4 }}>
            <Input label="Header" placeholder="x-team" />
            <Input label="Value" placeholder="payments-sre" />
            <Button variant="secondary" icon="plus" style={{ height: 38 }}>Add</Button>
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--slate-400)', marginTop: 12 }}>No custom headers configured.</div>
        </Card>
      </div>
    );
  }

  function AuditTab({ toggles, t }) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <Card title="Audit & Retention" subtitle="How long action history is kept, and where it is shipped.">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 8 }}>
            <Select label="Retention Period" options={['Forever', '365 days', '180 days', '90 days', '30 days']} />
            <Select label="Export Format" options={['CSV', 'JSON', 'NDJSON']} />
            <Input label="Current Log Size" value={`${D.audit.length} actions`} readOnly />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 16 }}>
            <ToggleRow locked label="Audit recovery actions" desc="Replay, park, and delete operations are always recorded." />
            <ToggleRow label="Audit read-only browsing" desc="Also record who viewed which queues and messages." checked={toggles.auditReads} onChange={t('auditReads')} />
            <ToggleRow label="Ship to syslog" desc="Forward audit entries to the configured syslog endpoint." checked={toggles.syslog} onChange={t('syslog')} />
          </div>
        </Card>
        <section style={{ background: 'var(--red-50)', border: '1px solid var(--red-200)', borderRadius: 'var(--radius-lg)', padding: 20 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--red-600)' }}>Danger Zone</div>
          <DangerRow title="Purge Audit Log" desc="Permanently delete all audit history. Exports are not affected." btn="Purge Log"
            onClick={() => window.alert('Purging the audit log means deleting data/queuelens.db on the server — it is not available from the UI so history cannot be erased by a compromised session.')} />
        </section>
      </div>
    );
  }

  function Configuration({ envs, activeEnv = 'development', vhost = '/', onEnvChange }) {
    const profiles = envs || [
      { id: B.environment || 'development', label: (B.environment || 'development').toUpperCase(), host: B.host, api: CFG.management_url, vhosts: [{ id: B.vhost || '/' }] },
    ];
    const [tab, setTab] = React.useState('conn');
    const [toggles, setToggles] = React.useState(loadToggles);
    const t = (k) => (v) => setToggles((s) => {
      const next = { ...s, [k]: v };
      try { localStorage.setItem('ql_settings', JSON.stringify(next)); } catch (e) {}
      return next;
    });
    const [test, setTest] = React.useState(null);
    const [testing, setTesting] = React.useState(false);
    const runTest = () => {
      setTesting(true);
      setTimeout(() => { setTest(window.QL.testConnection()); setTesting(false); }, 50);
    };
    React.useEffect(() => { runTest(); }, []);

    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageHeader title="Configuration" subtitle="Manage connection, safety defaults, limits, and system behavior." />
          <Tabs active={tab} onChange={setTab} tabs={[
            { id: 'conn', label: 'Connection', icon: 'plug' },
            { id: 'safety', label: 'Safety Defaults', icon: 'shield-check' },
            { id: 'limits', label: 'Limits & Timeouts', icon: 'clock' },
            { id: 'headers', label: 'Headers', icon: 'code' },
            { id: 'audit', label: 'Audit & Retention', icon: 'clipboard-list' }]} style={{ marginBottom: 22 }} />

          {tab === 'conn' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <Card title="Broker Connection" subtitle="How QueueLens connects to the RabbitMQ Management API (QUEUELENS_RABBITMQ_*).">
                <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr', gap: 14, marginTop: 8 }}>
                  <Input label="Management API URL" required value={CFG.management_url || ''} readOnly />
                  <Input label="Username" required value={window.QL.user || 'admin'} readOnly />
                  <Input label="Password" required type="password" value="••••••••••" readOnly />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr', gap: 14, marginTop: 14 }}>
                  <Select label="Virtual Host" options={[B.vhost || '/']} />
                  <Input label="Operation Timeout" value={String(CFG.operation_timeout_seconds ?? 10)} suffix="sec" readOnly />
                  <Input label="Connection Name" value={CFG.connection_name || 'queuelens'} readOnly />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 18 }}>
                  <Switch checked={(CFG.management_url || '').startsWith('https')} label="SSL / TLS" description="Enable TLS for Management API" />
                  <Switch checked={toggles.verify} onChange={t('verify')} label="Verify SSL Certificate" description="Verify server certificate (recommended)" />
                </div>
                <div style={{ display: 'flex', gap: 14, marginTop: 18, alignItems: 'stretch' }}>
                  <Button icon="plug-zap" onClick={runTest} disabled={testing}>{testing ? 'Testing…' : 'Test Connection'}</Button>
                  {test && (test.ok
                    ? <Alert tone="success" title="Connection successful" style={{ flex: 1, padding: '8px 14px' }}>Connected to {B.host} (vhost: {B.vhost || '/'}) in {test.latency_ms}ms</Alert>
                    : <Alert tone="danger" title="Connection problem" style={{ flex: 1, padding: '8px 14px' }}>Management API: {test.management_api ? 'ok' : 'unreachable'} · AMQP: {test.amqp ? 'ok' : 'down'}{test.error ? ' — ' + test.error : ''}</Alert>)}
                </div>
              </Card>

              <Card title="Environments & Virtual Hosts" subtitle="Connection profiles. Switch the active environment and vhost from the top bar — all views and audit entries are scoped to that selection."
                action={<Button variant="secondary" size="sm" icon="plus" onClick={() => window.alert('Environments are defined by QUEUELENS_* environment variables — run one QueueLens instance per broker (e.g. one compose service per environment).')}>Add Environment</Button>}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
                  {profiles.map((p) => <EnvRow key={p.id} p={p} active={p.id === activeEnv} vhost={vhost} onEnvChange={onEnvChange} />)}
                </div>
                <Alert tone="info" style={{ marginTop: 14 }}>
                  Credentials are stored per environment (as environment variables, never in the browser). Production requires type-to-confirm on every destructive action.
                </Alert>
              </Card>

              <Card title="General Settings" subtitle="General application settings and behavior.">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 8 }}>
                  <Input label="Environment" value={CFG.environment || 'development'} readOnly />
                  <Input label="Application Name" value={CFG.app_name || 'QueueLens'} readOnly />
                  <Input label="Refresh Interval" defaultValue="30" suffix="sec" />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 18 }}>
                  <Switch checked={toggles.auto} onChange={t('auto')} label="Auto Refresh" description="Automatically refresh data" />
                  <Switch checked={toggles.limits} onChange={t('limits')} label="Show Preview Limits" description="Display preview limit warnings" />
                  <Switch checked label="Confirm Dangerous Actions" description="Required — every destructive API call demands confirm: true" />
                </div>
              </Card>

              <section style={{ background: 'var(--red-50)', border: '1px solid var(--red-200)', borderRadius: 'var(--radius-lg)', padding: 20 }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--red-600)' }}>Danger Zone</div>
                <div style={{ fontSize: 13, color: 'var(--slate-600)', marginTop: 2 }}>Actions in this section can affect data. Please be careful.</div>
                <DangerRow title="Clear Local Cache" desc="Clear all locally cached data. This will force fresh data to be fetched from RabbitMQ." btn="Clear Cache"
                  onClick={() => location.reload()} />
                <DangerRow title="Reset Application Settings" desc="Reset all settings to their default values." btn="Reset Settings"
                  onClick={() => { localStorage.removeItem('ql_settings'); localStorage.removeItem('ql_alert_rules'); localStorage.removeItem('ql_quiet_hours'); location.reload(); }} />
              </section>
            </div>
          ) : tab === 'safety' ? (
            <SafetyTab toggles={toggles} t={t} />
          ) : tab === 'limits' ? (
            <LimitsTab />
          ) : tab === 'headers' ? (
            <HeadersTab />
          ) : (
            <AuditTab toggles={toggles} t={t} />
          )}
        </div>

        <div style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none', display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card title="Connection Status" action={<StatusPill tone={B.live ? 'success' : 'danger'}>{B.live ? 'Healthy' : 'Down'}</StatusPill>}>
            <SideRow icon="badge-check" label="Status" value={B.live ? 'Connected' : 'Disconnected'} />
            <SideRow icon="git-branch" label="Broker Version" value={(B.api || '').replace('RabbitMQ ', '') || '—'} />
            <SideRow icon="server" label="Management API" value={CFG.management_url || '—'} />
            <SideRow icon="home" label="Virtual Host" value={B.vhost || '/'} />
            <SideRow icon="users" label="Cluster Name" value={(test && test.cluster_name) || '—'} />
            <SideRow icon="layout-grid" label="Nodes" value={String((test && test.nodes) || 1)} />
            <SideRow icon="list" label="Queue Count" value={String((test && test.queues != null) ? test.queues : D.queues.length)} />
            <SideRow icon="clock" label="Last Check" value={test ? 'just now' : '—'} />
            <Button variant="secondary" iconRight="external-link" style={{ width: '100%', marginTop: 10, color: 'var(--text-link)' }}
              onClick={() => window.open(CFG.management_url, '_blank', 'noopener')}>View Broker Overview</Button>
          </Card>
          <Card title="Safety Defaults (Summary)">
            <CheckRow label="Publish-before-ack" right="Enabled" />
            <CheckRow label="Non-destructive browsing" right="Enabled" />
            <CheckRow label="Audit everything" right="Enabled" />
            <CheckRow label="Messages never lost" right="Enabled" />
            <Button variant="secondary" iconRight="chevron-right" style={{ width: '100%', marginTop: 10 }} onClick={() => setTab('safety')}>Manage Safety Defaults</Button>
          </Card>
          <Card title="Limits (Summary)">
            <SideRow icon="eye" label="Message Preview Limit" value={String(CFG.max_preview_messages ?? '—')} />
            <SideRow icon="file-text" label="Refetch Window" value={String(CFG.refetch_window_size ?? '—')} />
            <SideRow icon="layers" label="Max Bulk Selection" value={String(CFG.max_bulk_size ?? '—')} />
            <SideRow icon="clock" label="Operation Timeout" value={(CFG.operation_timeout_seconds ?? '—') + 's'} />
            <Button variant="secondary" iconRight="chevron-right" style={{ width: '100%', marginTop: 10 }} onClick={() => setTab('limits')}>Manage Limits &amp; Timeouts</Button>
          </Card>
        </div>
      </div>
    );
  }

  window.QL.screens.Configuration = Configuration;
})();
