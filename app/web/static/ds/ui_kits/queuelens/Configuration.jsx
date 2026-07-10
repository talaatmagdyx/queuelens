// Configuration screen — read-only view of the live runtime config (/api/config).
// QueueLens is configured via QUEUELENS_* environment variables; nothing here is editable.
(function () {
  const { Icon, Badge, StatusPill, Button, Alert, Tabs, Input, Switch } = window.__NS;
  const { PageHeader, Card } = window.QL;
  const D = window.QL.data;
  const B = window.QL.broker;

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

  function LockedRow({ label, desc }) {
    return (
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, padding: '14px 16px', background: 'var(--surface-card)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)' }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600, color: 'var(--slate-900)' }}>
            {label}
            <Badge tone="success" uppercase={false}>always on</Badge>
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 2 }}>{desc}</div>
        </div>
        <Switch checked />
      </div>
    );
  }

  const ENV_NOTE = (
    <Alert tone="info" style={{ marginTop: 14 }}>
      Configuration is read-only here — it comes from QUEUELENS_* environment variables. Change the
      variables and restart QueueLens to apply.
    </Alert>
  );

  function HeaderRow({ k, v }) {
    const cell = { padding: '10px 14px', fontSize: 13, fontFamily: 'var(--font-mono)', borderTop: '1px solid var(--slate-100)' };
    return (
      <tr>
        <td style={{ ...cell, color: 'var(--slate-600)' }}>{k}</td>
        <td style={{ ...cell, color: 'var(--slate-700)' }}>{v}</td>
        <td style={{ padding: '10px 14px', textAlign: 'right', borderTop: '1px solid var(--slate-100)' }}><Badge tone="success" uppercase={false}>always added</Badge></td>
      </tr>
    );
  }

  function Configuration() {
    const CFG = React.useMemo(() => window.QL.fetchConfig(), []);
    const [tab, setTab] = React.useState('conn');
    const [test, setTest] = React.useState(null);
    const [testing, setTesting] = React.useState(false);

    const runTest = () => {
      setTesting(true);
      setTimeout(() => { setTest(window.QL.testConnection()); setTesting(false); }, 50);
    };

    const nowIso = new Date().toISOString();

    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageHeader title="Configuration" subtitle="Runtime configuration, safety defaults, and limits — as loaded from the environment." />
          <Tabs active={tab} onChange={setTab} tabs={[
            { id: 'conn', label: 'Connection', icon: 'plug' },
            { id: 'safety', label: 'Safety Defaults', icon: 'shield-check' },
            { id: 'limits', label: 'Limits & Timeouts', icon: 'clock' },
            { id: 'headers', label: 'Headers', icon: 'code' },
            { id: 'audit', label: 'Audit & Retention', icon: 'clipboard-list' }]} style={{ marginBottom: 22 }} />

          {tab === 'conn' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <Card title="Broker Connection" subtitle="How QueueLens connects to RabbitMQ (QUEUELENS_RABBITMQ_*).">
                <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr', gap: 14, marginTop: 8 }}>
                  <Input label="Management API URL" value={CFG.management_url || '—'} readOnly />
                  <Input label="Virtual Host" value={CFG.vhost || '/'} readOnly />
                  <Input label="AMQP Host" value={B.host} readOnly />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr', gap: 14, marginTop: 14 }}>
                  <Input label="Connection Name" value={CFG.connection_name || 'queuelens'} readOnly />
                  <Input label="Environment" value={CFG.environment || '—'} readOnly />
                  <Input label="Authentication" value={CFG.auth_enabled ? 'Basic auth enabled' : 'Disabled'} readOnly />
                </div>
                <div style={{ display: 'flex', gap: 14, marginTop: 18, alignItems: 'stretch' }}>
                  <Button icon="plug-zap" onClick={runTest} disabled={testing}>{testing ? 'Testing…' : 'Test Connection'}</Button>
                  {test && (test.ok
                    ? <Alert tone="success" title="Connection successful" style={{ flex: 1, padding: '8px 14px' }}>
                        Management API + AMQP reachable in {test.latency_ms}ms — RabbitMQ {test.rabbitmq_version || '?'}{test.cluster_name ? ', cluster ' + test.cluster_name : ''}
                      </Alert>
                    : <Alert tone="danger" title="Connection problem" style={{ flex: 1, padding: '8px 14px' }}>
                        Management API: {test.management_api ? 'ok' : 'unreachable'} · AMQP: {test.amqp ? 'ok' : 'down'}{test.error ? ' — ' + test.error : ''}
                      </Alert>)}
                </div>
                {ENV_NOTE}
              </Card>
            </div>
          ) : tab === 'safety' ? (
            <Card title="Safety Defaults" subtitle="Core safety guarantees, built into every operation. They cannot be disabled.">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
                <LockedRow label="Publish-before-ack" desc="A message is only acknowledged after the broker confirms the publish to its destination." />
                <LockedRow label="Non-destructive browsing" desc="Browsing fetches messages with requeue — reading never removes anything." />
                <LockedRow label="Audit everything" desc="Every replay, park, and delete is recorded with user, queue, target, and outcome." />
                <LockedRow label="Confirm dangerous actions" desc="Every replay, park, delete, and bulk execute requires confirm: true; bulk requires a dry-run first." />
                <LockedRow label={'Payload masking' + (CFG.masking_enabled ? '' : ' (disabled)')} desc={CFG.masking_enabled ? 'Sensitive fields are masked in previews: ' + (CFG.masked_fields || []).join(', ') : 'QUEUELENS_MASKING_ENABLED is off — payloads are shown unmasked.'} />
              </div>
              <Alert tone="success" style={{ marginTop: 14 }}>Messages never lost: no QueueLens operation can drop a message — failed publishes never ack the original.</Alert>
            </Card>
          ) : tab === 'limits' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <Card title="Limits" subtitle="Caps that keep browsing and bulk operations safe on large queues.">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 8 }}>
                  <Input label="Message Preview Limit" value={String(CFG.max_preview_messages ?? '—')} suffix="msgs" readOnly />
                  <Input label="Refetch Window" value={String(CFG.refetch_window_size ?? '—')} suffix="msgs" readOnly />
                  <Input label="Max Bulk Size" value={String(CFG.max_bulk_size ?? '—')} suffix="msgs" readOnly />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 14 }}>
                  <Input label="Max Message Size" value={CFG.max_message_size_bytes ? (CFG.max_message_size_bytes / 1048576).toFixed(1) : '—'} suffix="MB" readOnly />
                  <Input label="Operation Timeout" value={String(CFG.operation_timeout_seconds ?? '—')} suffix="sec" readOnly />
                  <Input label="Bulk Dry-Run TTL" value={String(CFG.bulk_dry_run_ttl_seconds ?? '—')} suffix="sec" readOnly />
                </div>
                {ENV_NOTE}
              </Card>
            </div>
          ) : tab === 'headers' ? (
            <Card title="Replay Headers" subtitle="Headers QueueLens adds so replayed messages are traceable downstream. They are always added." pad={false}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    {['Header', 'Example Value', ''].map((h, i) => (
                      <th key={i} style={{ textAlign: i === 2 ? 'right' : 'left', padding: '8px 14px', fontSize: 12.5, fontWeight: 600, color: 'var(--slate-500)', background: 'var(--surface-table-header)' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <HeaderRow k="x-queuelens-replayed" v="true" />
                  <HeaderRow k="x-queuelens-source-queue" v={window.QL.defaultQueue || 'orders.dlq'} />
                  <HeaderRow k="x-queuelens-replayed-at" v={nowIso} />
                  <HeaderRow k="x-queuelens-action" v="replay_move" />
                  <HeaderRow k="x-queuelens-user" v={window.QL.user} />
                </tbody>
              </table>
            </Card>
          ) : (
            <Card title="Audit & Retention" subtitle="Every recovery action is recorded to the local SQLite audit store.">
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 8 }}>
                <Input label="Recorded Actions (visible)" value={String(D.audit.length)} readOnly />
                <Input label="Retention" value="Unbounded" readOnly />
                <Input label="Store" value="SQLite (local)" readOnly />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 16 }}>
                <LockedRow label="Audit recovery actions" desc="Replay, park, delete, and bulk operations are always recorded with attempt and outcome events." />
              </div>
              {ENV_NOTE}
            </Card>
          )}
        </div>

        <div style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none', display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card title="Connection Status" action={<StatusPill tone={B.live ? 'success' : 'danger'}>{B.live ? 'Healthy' : 'Down'}</StatusPill>}>
            <SideRow icon="badge-check" label="Status" value={B.live ? 'Connected' : 'Disconnected'} />
            <SideRow icon="git-branch" label="Broker Version" value={B.api.replace('RabbitMQ ', '')} />
            <SideRow icon="server" label="Management API" value={CFG.management_url || '—'} />
            <SideRow icon="home" label="Virtual Host" value={B.vhost} />
            <SideRow icon="list" label="Queue Count" value={String(D.queues.length)} />
            <SideRow icon="clock" label="API Latency" value={B.latency} />
          </Card>
          <Card title="Safety Defaults (Summary)">
            <CheckRow label="Publish-before-ack" right="Enabled" />
            <CheckRow label="Non-destructive browsing" right="Enabled" />
            <CheckRow label="Audit everything" right="Enabled" />
            <CheckRow label="Payload masking" right={CFG.masking_enabled ? 'Enabled' : 'Off'} />
          </Card>
          <Card title="Limits (Summary)">
            <SideRow icon="eye" label="Message Preview Limit" value={String(CFG.max_preview_messages ?? '—')} />
            <SideRow icon="layers" label="Max Bulk Size" value={String(CFG.max_bulk_size ?? '—')} />
            <SideRow icon="clock" label="Operation Timeout" value={(CFG.operation_timeout_seconds ?? '—') + 's'} />
            <SideRow icon="timer" label="Dry-Run TTL" value={(CFG.bulk_dry_run_ttl_seconds ?? '—') + 's'} />
          </Card>
        </div>
      </div>
    );
  }

  window.QL.screens.Configuration = Configuration;
})();
