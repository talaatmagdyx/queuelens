// Configuration screen with Connection Status side panel.
(function () {
  const { Icon, Badge, StatusPill, Button, Alert, Tabs, Select, Input, Switch, KeyValue } = window.__NS;
  const { PageHeader, Card, ArrowLink } = window.QL;

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

  function DangerRow({ title, desc, btn }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '14px 16px', background: 'var(--surface-card)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', marginTop: 10 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--slate-900)' }}>{title}</div>
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 2 }}>{desc}</div>
        </div>
        <Button variant="danger" size="sm">{btn}</Button>
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
            <ToggleRow label="Confirm dangerous actions" desc="Require confirmation for destructive actions." checked={toggles.confirmDanger} onChange={t('confirmDanger')} />
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
            <Input label="Message Preview Limit" defaultValue="100" suffix="msgs" />
            <Input label="Page Size" defaultValue="100" suffix="msgs" />
            <Input label="Max Bulk Selection" defaultValue="50" suffix="msgs" />
          </div>
          <Alert tone="info" style={{ marginTop: 14 }}>Preview limit caps how many messages are fetched (with requeue) per queue. Larger values increase broker load.</Alert>
        </Card>
        <Card title="Timeouts" subtitle="How long QueueLens waits on the Management API and publishes.">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 8 }}>
            <Input label="Connection Timeout" defaultValue="5" suffix="sec" />
            <Input label="Request Timeout" defaultValue="10" suffix="sec" />
            <Input label="Publish Confirm Timeout" defaultValue="5" suffix="sec" />
          </div>
        </Card>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <Button variant="secondary">Reset to Defaults</Button>
          <Button>Save Changes</Button>
        </div>
      </div>
    );
  }

  function HeaderToggleRow({ k, v, checked, onChange }) {
    return (
      <tr>
        <td style={{ padding: '10px 14px', fontSize: 13, fontFamily: 'var(--font-mono)', color: 'var(--slate-600)', borderTop: '1px solid var(--slate-100)' }}>{k}</td>
        <td style={{ padding: '10px 14px', fontSize: 13, fontFamily: 'var(--font-mono)', color: 'var(--slate-700)', borderTop: '1px solid var(--slate-100)' }}>{v}</td>
        <td style={{ padding: '10px 14px', textAlign: 'right', borderTop: '1px solid var(--slate-100)' }}><Switch checked={checked} onChange={onChange} /></td>
      </tr>
    );
  }

  function HeadersTab({ toggles, t }) {
    const th = { textAlign: 'left', padding: '8px 14px', fontSize: 12.5, fontWeight: 600, color: 'var(--slate-500)', background: 'var(--surface-table-header)' };
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <Card title="Replay Headers" subtitle="Headers QueueLens adds so replayed messages are traceable downstream." pad={false}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={th}>Header</th><th style={th}>Example Value</th><th style={{ ...th, textAlign: 'right' }}>Enabled</th></tr></thead>
            <tbody>
              <HeaderToggleRow k="x-queuelens-replayed" v="true" checked={toggles.hReplayed} onChange={t('hReplayed')} />
              <HeaderToggleRow k="x-queuelens-source-queue" v="payments.retry.dlq" checked={toggles.hSource} onChange={t('hSource')} />
              <HeaderToggleRow k="x-queuelens-replayed-at" v="2024-05-21T10:24:15.123Z" checked={toggles.hAt} onChange={t('hAt')} />
              <HeaderToggleRow k="x-queuelens-action" v="replay_move" checked={toggles.hAction} onChange={t('hAction')} />
              <HeaderToggleRow k="x-queuelens-user" v="admin" checked={toggles.hUser} onChange={t('hUser')} />
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
            <Select label="Retention Period" options={['90 days', '30 days', '180 days', '365 days', 'Forever']} />
            <Select label="Export Format" options={['CSV', 'JSON', 'NDJSON']} />
            <Input label="Current Log Size" defaultValue="152 actions · 1.2 MB" readOnly />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 16 }}>
            <ToggleRow locked label="Audit recovery actions" desc="Replay, park, and delete operations are always recorded." />
            <ToggleRow label="Audit read-only browsing" desc="Also record who viewed which queues and messages." checked={toggles.auditReads} onChange={t('auditReads')} />
            <ToggleRow label="Ship to syslog" desc="Forward audit entries to the configured syslog endpoint." checked={toggles.syslog} onChange={t('syslog')} />
          </div>
        </Card>
        <section style={{ background: 'var(--red-50)', border: '1px solid var(--red-200)', borderRadius: 'var(--radius-lg)', padding: 20 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--red-600)' }}>Danger Zone</div>
          <DangerRow title="Purge Audit Log" desc="Permanently delete all audit history. Exports are not affected." btn="Purge Log" />
        </section>
      </div>
    );
  }

  function Configuration({ envs, activeEnv = 'development', vhost = '/', onEnvChange }) {
    const profiles = envs || [
      { id: 'development', label: 'DEVELOPMENT', host: 'rabbitmq:5672', api: 'http://rabbitmq:15672', vhosts: [{ id: '/' }, { id: '/payments' }] },
      { id: 'staging', label: 'STAGING', host: 'rabbitmq-stg:5672', api: 'http://rabbitmq-stg:15672', vhosts: [{ id: '/' }, { id: '/payments' }] },
      { id: 'production', label: 'PRODUCTION', host: 'rabbitmq-prod:5672', api: 'https://rabbitmq-prod:15672', vhosts: [{ id: '/' }, { id: '/payments' }, { id: '/orders' }] },
    ];
    const [tab, setTab] = React.useState('conn');
    const [toggles, setToggles] = React.useState({ tls: false, verify: false, auto: true, limits: true, confirm: true, confirmDanger: true, typeConfirm: true, preferPark: true, hReplayed: true, hSource: true, hAt: true, hAction: true, hUser: false, auditReads: false, syslog: true });
    const t = (k) => (v) => setToggles((s) => ({ ...s, [k]: v }));
    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageHeader title="Configuration" subtitle="Manage connection, safety defaults, limits, and system behavior." />
          <Tabs active={tab} onChange={setTab} tabs={[
            { id: 'conn', label: 'Connection', icon: 'plug' },
            { id: 'safety', label: 'Safety Defaults', icon: 'shield-check' },
            { id: 'limits', label: 'Limits & Timeouts', icon: 'clock' },
            { id: 'headers', label: 'Headers', icon: 'code' },
            { id: 'audit', label: 'Audit & Retention', icon: 'clipboard-list' },
            { id: 'adv', label: 'Advanced', icon: 'settings' }]} style={{ marginBottom: 22 }} />

          {tab === 'conn' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <Card title="Broker Connection" subtitle="Configure how QueueLens connects to RabbitMQ Management API.">
                <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr', gap: 14, marginTop: 8 }}>
                  <Input label="Management API URL" required defaultValue="http://rabbitmq:15672" />
                  <Input label="Username" required defaultValue="admin" />
                  <Input label="Password" required type="password" defaultValue="secretpassword" />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr', gap: 14, marginTop: 14 }}>
                  <Select label="Virtual Host" options={['/']} />
                  <Input label="Connection Timeout" defaultValue="5" suffix="sec" />
                  <Input label="Request Timeout" defaultValue="10" suffix="sec" />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 18 }}>
                  <Switch checked={toggles.tls} onChange={t('tls')} label="SSL / TLS" description="Enable TLS for Management API" />
                  <Switch checked={toggles.verify} onChange={t('verify')} label="Verify SSL Certificate" description="Verify server certificate (recommended)" />
                </div>
                <div style={{ display: 'flex', gap: 14, marginTop: 18, alignItems: 'stretch' }}>
                  <Button icon="plug-zap">Test Connection</Button>
                  <Alert tone="success" title="Connection successful" style={{ flex: 1, padding: '8px 14px' }}>Connected to rabbitmq:15672 (vhost: /) in 42ms</Alert>
                </div>
              </Card>

              <Card title="Environments & Virtual Hosts" subtitle="Connection profiles. Switch the active environment and vhost from the top bar — all views and audit entries are scoped to that selection."
                action={<Button variant="secondary" size="sm" icon="plus">Add Environment</Button>}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
                  {profiles.map((p) => <EnvRow key={p.id} p={p} active={p.id === activeEnv} vhost={vhost} onEnvChange={onEnvChange} />)}
                </div>
                <Alert tone="info" style={{ marginTop: 14 }}>
                  Credentials are stored per environment. Production requires type-to-confirm on every destructive action.
                </Alert>
              </Card>

              <Card title="General Settings" subtitle="General application settings and behavior.">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 8 }}>
                  <Select label="Environment" options={['development', 'staging', 'production']} />
                  <Input label="Application Name" defaultValue="QueueLens" />
                  <Input label="Refresh Interval" defaultValue="5" suffix="sec" />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 18 }}>
                  <Switch checked={toggles.auto} onChange={t('auto')} label="Auto Refresh" description="Automatically refresh data" />
                  <Switch checked={toggles.limits} onChange={t('limits')} label="Show Preview Limits" description="Display preview limit warnings" />
                  <Switch checked={toggles.confirm} onChange={t('confirm')} label="Confirm Dangerous Actions" description="Require confirmation for destructive actions" />
                </div>
              </Card>

              <section style={{ background: 'var(--red-50)', border: '1px solid var(--red-200)', borderRadius: 'var(--radius-lg)', padding: 20 }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--red-600)' }}>Danger Zone</div>
                <div style={{ fontSize: 13, color: 'var(--slate-600)', marginTop: 2 }}>Actions in this section can affect data. Please be careful.</div>
                <DangerRow title="Clear Local Cache" desc="Clear all locally cached data. This will force fresh data to be fetched from RabbitMQ." btn="Clear Cache" />
                <DangerRow title="Reset Application Settings" desc="Reset all settings to their default values." btn="Reset Settings" />
              </section>
            </div>
          ) : tab === 'safety' ? (
            <SafetyTab toggles={toggles} t={t} />
          ) : tab === 'limits' ? (
            <LimitsTab />
          ) : tab === 'headers' ? (
            <HeadersTab toggles={toggles} t={t} />
          ) : tab === 'audit' ? (
            <AuditTab toggles={toggles} t={t} />
          ) : (
            <Card>
              <div style={{ padding: '40px 0', textAlign: 'center', fontSize: 13.5, color: 'var(--slate-400)' }}>
                This tab was not present in the source screenshots — intentionally left as a stub.
              </div>
            </Card>
          )}
        </div>

        <div style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none', display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card title="Connection Status" action={<StatusPill tone="success">Healthy</StatusPill>}>
            <SideRow icon="badge-check" label="Status" value="Connected" />
            <SideRow icon="git-branch" label="Broker Version" value="3.13.2" />
            <SideRow icon="server" label="Management API" value="http://rabbitmq:15672" />
            <SideRow icon="home" label="Virtual Host" value="/" />
            <SideRow icon="users" label="Cluster Name" value="rabbit@rabbitmq" />
            <SideRow icon="layout-grid" label="Nodes" value="1" />
            <SideRow icon="list" label="Queue Count" value="24" />
            <SideRow icon="clock" label="Last Check" value="7s ago" />
            <Button variant="secondary" iconRight="external-link" style={{ width: '100%', marginTop: 10, color: 'var(--text-link)' }}>View Broker Overview</Button>
          </Card>
          <Card title="Safety Defaults (Summary)">
            <CheckRow label="Publish-before-ack" right="Enabled" />
            <CheckRow label="Non-destructive browsing" right="Enabled" />
            <CheckRow label="Audit everything" right="Enabled" />
            <CheckRow label="Messages never lost" right="Enabled" />
            <Button variant="secondary" iconRight="chevron-right" style={{ width: '100%', marginTop: 10 }}>Manage Safety Defaults</Button>
          </Card>
          <Card title="Limits (Summary)">
            <SideRow icon="eye" label="Message Preview Limit" value="100" />
            <SideRow icon="file-text" label="Page Size" value="100" />
            <SideRow icon="clock" label="Request Timeout" value="10s" />
            <SideRow icon="timer" label="Connection Timeout" value="5s" />
            <Button variant="secondary" iconRight="chevron-right" style={{ width: '100%', marginTop: 10 }}>Manage Limits &amp; Timeouts</Button>
          </Card>
        </div>
      </div>
    );
  }

  window.QL.screens.Configuration = Configuration;
})();
