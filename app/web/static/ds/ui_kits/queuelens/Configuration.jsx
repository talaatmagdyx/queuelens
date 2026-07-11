// Configuration screen — full design layout, server-backed.
// UI toggles / custom headers / limits / retention persist via /api/settings;
// environments & vhosts come from /api/environments and Set Active really switches.
(function () {
  const { Icon, Badge, StatusPill, Button, Alert, Tabs, Select, Input, Switch, IconButton } = window.__NS;
  const { PageHeader, Card } = window.QL;
  const D = window.QL.data;
  const B = window.QL.broker || {};
  const CFG = window.QL.config || {};

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

  function EnvRow({ env, onActivate, onAddVhost, onRemove }) {
    const [adding, setAdding] = React.useState(false);
    const [vhostDraft, setVhostDraft] = React.useState('');
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '14px 16px', background: env.active ? 'var(--blue-50)' : 'var(--surface-card)', border: `1px solid ${env.active ? 'var(--blue-200)' : 'var(--border-default)'}`, borderRadius: 'var(--radius-md)' }}>
        <StatusPill tone={env.id === 'production' ? 'danger' : 'info'} dot size="sm" style={{ textTransform: 'uppercase', letterSpacing: '0.04em', flex: 'none' }}>{env.id}</StatusPill>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--slate-900)', fontFamily: 'var(--font-mono)' }}>{env.api}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>vhosts:</span>
            {env.vhosts.map((v) => {
              const isActive = env.active && v === env.active_vhost;
              return (
                <button key={v} onClick={() => onActivate(env.id, v)}
                  style={{ padding: '2px 8px', borderRadius: 6, border: 'none', cursor: 'pointer', background: isActive ? 'var(--blue-100)' : 'var(--slate-100)', color: isActive ? 'var(--blue-700)' : 'var(--slate-600)', fontSize: 12, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{v}</button>
              );
            })}
            {adding ? (
              <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                <input autoFocus value={vhostDraft} onChange={(e) => setVhostDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && vhostDraft.trim()) { onAddVhost(env.id, vhostDraft.trim()); setAdding(false); setVhostDraft(''); } if (e.key === 'Escape') setAdding(false); }}
                  placeholder="vhost name" style={{ width: 110, padding: '2px 8px', borderRadius: 6, border: '1px solid var(--blue-200)', fontSize: 12, fontFamily: 'var(--font-mono)', outline: 'none' }} />
                <button onClick={() => { if (vhostDraft.trim()) { onAddVhost(env.id, vhostDraft.trim()); setAdding(false); setVhostDraft(''); } }}
                  style={{ padding: '2px 8px', borderRadius: 6, border: 'none', cursor: 'pointer', background: 'var(--blue-600)', color: 'white', fontSize: 12, fontWeight: 600 }}>Add</button>
              </span>
            ) : (
              <button onClick={() => setAdding(true)} title="Add a vhost to this environment"
                style={{ padding: '2px 8px', borderRadius: 6, border: '1px dashed var(--slate-300)', cursor: 'pointer', background: 'transparent', color: 'var(--slate-500)', fontSize: 12, fontWeight: 600 }}>+ vhost</button>
            )}
          </div>
        </div>
        {env.active
          ? <StatusPill tone="success">Active</StatusPill>
          : <span style={{ display: 'inline-flex', gap: 6 }}>
              <Button variant="secondary" size="sm" onClick={() => onActivate(env.id, null)}>Set Active</Button>
              {env.removable && <IconButton icon="trash-2" size={30} onClick={() => onRemove(env.id)} />}
            </span>}
      </div>
    );
  }

  function Configuration() {
    const [serverSettings, setServerSettings] = React.useState(() => window.QL.fetchServerSettings());
    const [envs, setEnvs] = React.useState(() => window.QL.fetchEnvironments());
    const ui = serverSettings.ui || {};
    const limitsStored = serverSettings.limits || {};
    const retention = serverSettings.retention || {};
    const customHeaders = serverSettings.custom_headers || [];
    const [tab, setTab] = React.useState('conn');
    const [error, setError] = React.useState(null);
    const [savedNote, setSavedNote] = React.useState(null);
    const [test, setTest] = React.useState(null);
    const [testing, setTesting] = React.useState(false);
    const [switching, setSwitching] = React.useState(false);
    const [headerDraft, setHeaderDraft] = React.useState({ key: '', value: '' });
    const [limitsDraft, setLimitsDraft] = React.useState({
      max_preview_messages: limitsStored.max_preview_messages || CFG.max_preview_messages || 100,
      refetch_window_size: limitsStored.refetch_window_size || CFG.refetch_window_size || 100,
      max_bulk_size: limitsStored.max_bulk_size || CFG.max_bulk_size || 500,
    });

    const save = async (values, note) => {
      setError(null);
      try {
        const next = await window.QL.saveSettings(values);
        setServerSettings(next);
        setSavedNote(note || 'Saved');
        setTimeout(() => setSavedNote(null), 2500);
      } catch (e) { setError(e.message); }
    };
    const t = (k) => (v) => save({ ui: { ...ui, [k]: v } });

    const runTest = () => {
      setTesting(true);
      setTimeout(() => { setTest(window.QL.testConnection()); setTesting(false); }, 50);
    };
    React.useEffect(() => { runTest(); }, []);

    const activate = async (envId, vhost) => {
      setSwitching(true);
      setError(null);
      try {
        await window.QL.postJson('/api/environments/activate', { environment: envId, vhost: vhost || undefined });
        location.reload(); // the data layer reloads against the newly active broker/vhost
      } catch (e) { setError(e.message); setSwitching(false); }
    };

    const [addingEnv, setAddingEnv] = React.useState(false);
    const [envDraft, setEnvDraft] = React.useState({ name: '', vhosts: '/', host: '', management_url: '', username: '', password: '' });
    const removeEnv = async (envId) => {
      if (!window.confirm(`Remove environment "${envId}"? Its stored profile is deleted (the broker itself is untouched).`)) return;
      setError(null);
      try {
        const result = await window.QL.requestJson('DELETE', '/api/environments/' + encodeURIComponent(envId));
        setEnvs(result.environments);
      } catch (e) { setError(e.message); }
    };
    const addEnvironment = async () => {
      setError(null);
      try {
        const result = await window.QL.postJson('/api/environments', {
          name: envDraft.name.trim(),
          vhosts: envDraft.vhosts.split(',').map((v) => v.trim()).filter(Boolean),
          host: envDraft.host.trim() || null,
          management_url: envDraft.management_url.trim() || null,
          username: envDraft.username.trim() || null,
          password: envDraft.password || null,
        });
        setEnvs(result.environments);
        setAddingEnv(false);
        setEnvDraft({ name: '', vhosts: '/', host: '', management_url: '', username: '', password: '' });
        setSavedNote('Environment added');
        setTimeout(() => setSavedNote(null), 2500);
      } catch (e) { setError(e.message); }
    };
    const addVhost = async (envId, vhost) => {
      setError(null);
      try {
        const result = await window.QL.postJson('/api/environments', { name: envId, vhosts: [vhost] });
        setEnvs(result.environments);
      } catch (e) { setError(e.message); }
    };

    const addHeader = () => {
      if (!headerDraft.key.trim()) return;
      save({ custom_headers: [...customHeaders, { key: headerDraft.key.trim(), value: headerDraft.value }] }, 'Header added');
      setHeaderDraft({ key: '', value: '' });
    };
    const removeHeader = (index) =>
      save({ custom_headers: customHeaders.filter((_, i) => i !== index) }, 'Header removed');

    // The management URL points at the Docker-internal hostname; the browser needs
    // the host it is actually browsing from (override persisted in settings.ui).
    const derivedOverview = (() => {
      try {
        const u = new URL(CFG.management_url);
        return location.protocol + '//' + location.hostname + ':' + (u.port || '15672');
      } catch (e) { return 'http://localhost:15672'; }
    })();
    const overviewUrl = ui.broker_overview_url || derivedOverview;
    const [overviewDraft, setOverviewDraft] = React.useState(null); // null = not editing

    const th = { textAlign: 'left', padding: '8px 14px', fontSize: 12.5, fontWeight: 600, color: 'var(--slate-500)', background: 'var(--surface-table-header)' };
    const td = { padding: '10px 14px', fontSize: 13, fontFamily: 'var(--font-mono)', borderTop: '1px solid var(--slate-100)' };
    const nowIso = new Date().toISOString();

    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageHeader title="Configuration" subtitle="Manage connection, safety defaults, limits, and system behavior."
            after={savedNote && <StatusPill tone="success" size="sm">{savedNote}</StatusPill>} />
          {error && <Alert tone="danger" style={{ marginBottom: 14 }}>{error}</Alert>}
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
                  <Input label="Active Virtual Host" value={B.vhost || '/'} readOnly />
                  <Input label="Operation Timeout" value={String(CFG.operation_timeout_seconds ?? 10)} suffix="sec" readOnly />
                  <Input label="Connection Name" value={CFG.connection_name || 'queuelens'} readOnly />
                </div>
                <div style={{ display: 'flex', gap: 14, marginTop: 18, alignItems: 'stretch' }}>
                  <Button icon="plug-zap" onClick={runTest} disabled={testing}>{testing ? 'Testing…' : 'Test Connection'}</Button>
                  {test && (test.ok
                    ? <Alert tone="success" title="Connection successful" style={{ flex: 1, padding: '8px 14px' }}>Connected to {B.host} (vhost: {B.vhost || '/'}) in {test.latency_ms}ms</Alert>
                    : <Alert tone="danger" title="Connection problem" style={{ flex: 1, padding: '8px 14px' }}>Management API: {test.management_api ? 'ok' : 'unreachable'} · AMQP: {test.amqp ? 'ok' : 'down'}{test.error ? ' — ' + test.error : ''}</Alert>)}
                </div>
              </Card>

              <Card title="Environments & Virtual Hosts" subtitle="Set Active switches every view, action, and audit entry to that broker/vhost. Same-broker environments and vhosts can be added right here."
                action={<span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                  {switching && <StatusPill tone="warning" size="sm">Switching…</StatusPill>}
                  <Button variant="secondary" size="sm" icon="plus" onClick={() => setAddingEnv(!addingEnv)}>Add Environment</Button>
                </span>}>
                {addingEnv && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 14, padding: '12px 14px', background: 'var(--blue-50)', border: '1px solid var(--blue-200)', borderRadius: 'var(--radius-md)' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 12 }}>
                      <Input label="Name" required placeholder="staging-2" value={envDraft.name} onChange={(v) => setEnvDraft({ ...envDraft, name: v })} />
                      <Input label="Vhosts (comma-separated)" required placeholder="orders, payments, billing" value={envDraft.vhosts} onChange={(v) => setEnvDraft({ ...envDraft, vhosts: v })} />
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr 0.8fr 0.8fr', gap: 12 }}>
                      <Input label="Broker Host (optional)" placeholder="rabbitmq-stg2:5672" value={envDraft.host} onChange={(v) => setEnvDraft({ ...envDraft, host: v })} />
                      <Input label="Management URL (optional)" placeholder="http://rabbitmq-stg2:15672" value={envDraft.management_url} onChange={(v) => setEnvDraft({ ...envDraft, management_url: v })} />
                      <Input label="Username (optional)" placeholder="queuelens" value={envDraft.username} onChange={(v) => setEnvDraft({ ...envDraft, username: v })} />
                      <Input label="Password" type="password" value={envDraft.password} onChange={(v) => setEnvDraft({ ...envDraft, password: v })} />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <span style={{ flex: 1, fontSize: 12, color: 'var(--slate-500)' }}>
                        Leave the broker fields blank to share the default broker. Credentials are stored
                        server-side and never echoed back to the browser.
                      </span>
                      <Button variant="ghost" size="sm" onClick={() => setAddingEnv(false)}>Cancel</Button>
                      <Button style={{ height: 34 }} disabled={!envDraft.name.trim() || !envDraft.vhosts.trim()} onClick={addEnvironment}>Create</Button>
                    </div>
                  </div>
                )}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
                  {envs.map((env) => <EnvRow key={env.id} env={env} onActivate={activate} onAddVhost={addVhost} onRemove={removeEnv} />)}
                </div>
                <Alert tone="info" style={{ marginTop: 14 }}>
                  Environments added here share the default broker credentials (stored in environment
                  variables, never the browser). Profiles with their own broker or credentials belong in
                  QUEUELENS_ENVIRONMENTS_JSON. Named vhosts are created on the broker on first activation;
                  every switch is audited.
                </Alert>
              </Card>

              <Card title="General Settings" subtitle="General application settings and behavior. Saved on the server.">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 8 }}>
                  <Input label="Environment" value={CFG.environment || 'development'} readOnly />
                  <Input label="Application Name" value={CFG.app_name || 'QueueLens'} readOnly />
                  <Input label="Alert Evaluation Interval" value="15" suffix="sec" readOnly />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 18 }}>
                  <Switch checked={ui.auto !== false} onChange={t('auto')} label="Auto Refresh" description="Automatically refresh the dashboard" />
                  <Switch checked={ui.limits !== false} onChange={t('limits')} label="Show Preview Limits" description="Display preview limit warnings" />
                  <Switch checked label="Confirm Dangerous Actions" description="Required — every destructive API call demands confirm: true" />
                </div>
              </Card>

              <section style={{ background: 'var(--red-50)', border: '1px solid var(--red-200)', borderRadius: 'var(--radius-lg)', padding: 20 }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--red-600)' }}>Danger Zone</div>
                <div style={{ fontSize: 13, color: 'var(--slate-600)', marginTop: 2 }}>Actions in this section can affect data. Please be careful.</div>
                <DangerRow title="Clear Local Cache" desc="Reload fresh data from RabbitMQ." btn="Clear Cache" onClick={() => location.reload()} />
                <DangerRow title="Reset Application Settings" desc="Reset server-stored settings (UI, custom headers, limits, retention) to defaults." btn="Reset Settings"
                  onClick={() => { if (window.confirm('Reset all server-stored settings?')) save({ ui: {}, custom_headers: [], limits: {}, retention: {} }, 'Settings reset').then(() => location.reload()); }} />
              </section>
            </div>
          ) : tab === 'safety' ? (
            <Card title="Safety Defaults" subtitle="Core safety guarantees. Locked defaults cannot be disabled.">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
                <ToggleRow locked label="Publish-before-ack" desc="A message is only acknowledged after the broker confirms the publish to its destination." />
                <ToggleRow locked label="Non-destructive browsing" desc="Browsing fetches messages with requeue — reading never removes anything." />
                <ToggleRow locked label="Audit everything" desc="Every replay, park, delete, publish, and environment switch is recorded." />
                <ToggleRow locked label="Confirm dangerous actions" desc="Every destructive API call requires confirm: true." />
                <ToggleRow label="Type-to-confirm in production" desc="Require typing the queue name before any action in production environments." checked={ui.typeConfirm !== false} onChange={t('typeConfirm')} />
                <ToggleRow label="Prefer Park over Delete" desc="Suggest parking when a delete is requested on messages with x-death ≥ 3." checked={ui.preferPark !== false} onChange={t('preferPark')} />
              </div>
              <Alert tone="success" style={{ marginTop: 14 }}>Messages never lost: with these defaults, no QueueLens operation can drop a message.</Alert>
            </Card>
          ) : tab === 'limits' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <Card title="Limits" subtitle="Caps that keep browsing and bulk operations safe on large queues. The preview limit applies server-side immediately.">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 8 }}>
                  <Input label="Message Preview Limit" value={String(limitsDraft.max_preview_messages)} onChange={(v) => setLimitsDraft({ ...limitsDraft, max_preview_messages: parseInt(v, 10) || 0 })} suffix="msgs" />
                  <Input label="Refetch Window" value={String(limitsDraft.refetch_window_size)} onChange={(v) => setLimitsDraft({ ...limitsDraft, refetch_window_size: parseInt(v, 10) || 0 })} suffix="msgs" />
                  <Input label="Max Bulk Selection" value={String(limitsDraft.max_bulk_size)} onChange={(v) => setLimitsDraft({ ...limitsDraft, max_bulk_size: parseInt(v, 10) || 0 })} suffix="msgs" />
                </div>
                <Alert tone="info" style={{ marginTop: 14 }}>Preview limit caps how many messages are fetched (with requeue) per queue. Larger values increase broker load.</Alert>
              </Card>
              <Card title="Timeouts" subtitle="Set by QUEUELENS_* environment variables (restart to change).">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 8 }}>
                  <Input label="Operation Timeout" value={String(CFG.operation_timeout_seconds ?? 10)} suffix="sec" readOnly />
                  <Input label="Bulk Dry-Run TTL" value={String(CFG.bulk_dry_run_ttl_seconds ?? 600)} suffix="sec" readOnly />
                  <Input label="Max Message Size" value={CFG.max_message_size_bytes ? String(Math.round(CFG.max_message_size_bytes / 1024)) : '1024'} suffix="KB" readOnly />
                </div>
              </Card>
              <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                <Button variant="secondary" onClick={() => setLimitsDraft({ max_preview_messages: CFG.max_preview_messages || 100, refetch_window_size: CFG.refetch_window_size || 100, max_bulk_size: CFG.max_bulk_size || 500 })}>Reset to Defaults</Button>
                <Button onClick={() => save({ limits: limitsDraft }, 'Limits saved')}>Save Changes</Button>
              </div>
            </div>
          ) : tab === 'headers' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <Card title="Replay Headers" subtitle="Provenance headers QueueLens always adds so replayed messages are traceable downstream." pad={false}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr><th style={th}>Header</th><th style={th}>Example Value</th><th style={{ ...th, textAlign: 'right' }}>Enabled</th></tr></thead>
                  <tbody>
                    {[['x-queuelens-replayed', 'true'], ['x-queuelens-source-queue', window.QL.defaultQueue || 'orders.dlq'], ['x-queuelens-replayed-at', nowIso], ['x-queuelens-action', 'replay_move'], ['x-queuelens-replayed-by', window.QL.user || 'admin'], ['x-queuelens-original-fingerprint', 'c1f7c3d261f5cca2…']].map(([k, v]) => (
                      <tr key={k}>
                        <td style={{ ...td, color: 'var(--slate-600)' }}>{k}</td>
                        <td style={{ ...td, color: 'var(--slate-700)' }}>{v}</td>
                        <td style={{ ...td, textAlign: 'right' }}><Badge tone="success" uppercase={false}>always added</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
              <Card title="Custom Headers" subtitle="Stored on the server and stamped on every message QueueLens replays, parks, or publishes.">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 12, alignItems: 'end', marginTop: 4 }}>
                  <Input label="Header" placeholder="x-team" value={headerDraft.key} onChange={(v) => setHeaderDraft({ ...headerDraft, key: v })} />
                  <Input label="Value" placeholder="payments-sre" value={headerDraft.value} onChange={(v) => setHeaderDraft({ ...headerDraft, value: v })} />
                  <Button variant="secondary" icon="plus" style={{ height: 38 }} onClick={addHeader}>Add</Button>
                </div>
                {customHeaders.length === 0 ? (
                  <div style={{ fontSize: 12.5, color: 'var(--slate-400)', marginTop: 12 }}>No custom headers configured.</div>
                ) : (
                  <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 14 }}>
                    <tbody>
                      {customHeaders.map((h, i) => (
                        <tr key={h.key + i}>
                          <td style={{ ...td, color: 'var(--slate-600)' }}>{h.key}</td>
                          <td style={{ ...td, color: 'var(--slate-700)' }}>{h.value}</td>
                          <td style={{ ...td, textAlign: 'right' }}><IconButton icon="trash-2" size={26} onClick={() => removeHeader(i)} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Card>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <Card title="Audit & Retention" subtitle="How long action history and notifications are kept. Cleanup runs hourly on the server.">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginTop: 8 }}>
                  <Select label="Retention Period" value={retention.days ? retention.days + ' days' : 'Forever'}
                    onChange={(v) => save({ retention: { days: v === 'Forever' ? 0 : parseInt(v, 10) } }, 'Retention saved')}
                    options={['Forever', '365 days', '180 days', '90 days', '30 days']} />
                  <Select label="Export Format" value={ui.export_format || 'CSV'} onChange={(v) => save({ ui: { ...ui, export_format: v } })} options={['CSV', 'JSON']} />
                  <Input label="Current Log Size" value={`${D.audit.length} actions`} readOnly />
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 16 }}>
                  <ToggleRow locked label="Audit recovery actions" desc="Replay, park, delete, publish, and bulk operations are always recorded." />
                  <ToggleRow label="Ship to syslog" desc="Forward audit entries to the configured syslog endpoint (requires a syslog sidecar)." checked={!!ui.syslog} onChange={t('syslog')} />
                </div>
              </Card>
              <section style={{ background: 'var(--red-50)', border: '1px solid var(--red-200)', borderRadius: 'var(--radius-lg)', padding: 20 }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--red-600)' }}>Danger Zone</div>
                <DangerRow title="Purge Audit Log" desc="Audit history is immutable from the UI — set a retention period above and the server prunes it on schedule." btn="Purge Log"
                  onClick={() => window.alert('Audit history cannot be purged from the UI (so a compromised session cannot erase its tracks). Set a retention period instead.')} />
              </section>
            </div>
          )}
        </div>

        <div style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none', display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card title="Connection Status" action={<StatusPill tone={B.live ? 'success' : 'danger'}>{B.live ? 'Healthy' : 'Down'}</StatusPill>}>
            <SideRow icon="badge-check" label="Status" value={B.live ? 'Connected' : 'Disconnected'} />
            <SideRow icon="git-branch" label="Broker Version" value={(B.api || '').replace('RabbitMQ ', '') || '—'} />
            <SideRow icon="server" label="Management API" value={CFG.management_url || '—'} />
            <SideRow icon="home" label="Virtual Host" value={B.vhost || '/'} />
            <SideRow icon="users" label="Cluster Name" value={(test && test.cluster_name) || '—'} />
            <SideRow icon="list" label="Queue Count" value={String((test && test.queues != null) ? test.queues : D.queues.length)} />
            <SideRow icon="clock" label="Last Check" value={test ? 'just now' : '—'} />
            <Button variant="secondary" iconRight="external-link" style={{ width: '100%', marginTop: 10, color: 'var(--text-link)' }}
              onClick={() => window.open(overviewUrl, '_blank', 'noopener')}>View Broker Overview</Button>
            {overviewDraft === null ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8 }}>
                <span style={{ flex: 1, fontSize: 11.5, fontFamily: 'var(--font-mono)', color: 'var(--slate-400)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{overviewUrl}</span>
                <a href="#" onClick={(e) => { e.preventDefault(); setOverviewDraft(overviewUrl); }} style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-link)', textDecoration: 'none', flex: 'none' }}>Change URL</a>
              </div>
            ) : (
              <div style={{ marginTop: 8 }}>
                <Input label="Broker Overview URL (opens in your browser)" value={overviewDraft} onChange={setOverviewDraft} placeholder={derivedOverview} />
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 8 }}>
                  <Button variant="ghost" size="sm" onClick={() => setOverviewDraft(null)}>Cancel</Button>
                  <Button size="sm" onClick={() => { save({ ui: { ...ui, broker_overview_url: overviewDraft.trim() } }, 'URL saved'); setOverviewDraft(null); }}>Save</Button>
                </div>
              </div>
            )}
          </Card>
          <Card title="Safety Defaults (Summary)">
            <CheckRow label="Publish-before-ack" right="Enabled" />
            <CheckRow label="Non-destructive browsing" right="Enabled" />
            <CheckRow label="Audit everything" right="Enabled" />
            <CheckRow label="Messages never lost" right="Enabled" />
            <Button variant="secondary" iconRight="chevron-right" style={{ width: '100%', marginTop: 10 }} onClick={() => setTab('safety')}>Manage Safety Defaults</Button>
          </Card>
          <Card title="Limits (Summary)">
            <SideRow icon="eye" label="Message Preview Limit" value={String(limitsStored.max_preview_messages || CFG.max_preview_messages || '—')} />
            <SideRow icon="file-text" label="Refetch Window" value={String(limitsStored.refetch_window_size || CFG.refetch_window_size || '—')} />
            <SideRow icon="layers" label="Max Bulk Selection" value={String(limitsStored.max_bulk_size || CFG.max_bulk_size || '—')} />
            <SideRow icon="clock" label="Operation Timeout" value={(CFG.operation_timeout_seconds ?? '—') + 's'} />
            <Button variant="secondary" iconRight="chevron-right" style={{ width: '100%', marginTop: 10 }} onClick={() => setTab('limits')}>Manage Limits &amp; Timeouts</Button>
          </Card>
        </div>
      </div>
    );
  }

  window.QL.screens.Configuration = Configuration;
})();
