// Alerts screen — server-backed rules (CRUD via /api/alerts), delivery channels
// configured through /api/settings, evaluated by the backend alert engine.
(function () {
  const { Icon, StatusPill, Button, IconButton, DataTable, Input, Select, Switch, Checkbox, Alert } = window.__NS;
  const { PageHeader, Card, EmptyState } = window.QL;

  const SEV_TONE = { Alert: 'danger', Warning: 'warning', Info: 'info' };
  const METRIC_LABEL = {
    messages_ready: 'Messages ready', messages: 'Messages total',
    consumers: 'Consumers', publish_rate: 'Message rate (in)',
  };
  const METRIC_KEY = Object.fromEntries(Object.entries(METRIC_LABEL).map(([k, v]) => [v, k]));
  const DUR_LABEL = { 0: 'immediately', 60: 'for 1 minute', 300: 'for 5 minutes', 900: 'for 15 minutes', 3600: 'for 1 hour' };
  const DUR_KEY = Object.fromEntries(Object.entries(DUR_LABEL).map(([k, v]) => [v, Number(k)]));
  const CHANNEL_META = {
    email: { icon: 'mail', name: 'Email' },
    slack: { icon: 'hash', name: 'Slack' },
    pagerduty: { icon: 'phone-call', name: 'PagerDuty' },
    webhook: { icon: 'webhook', name: 'Webhook' },
  };

  function rel(iso) {
    if (!iso) return '—';
    const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
    if (isNaN(m)) return '—';
    if (m < 1) return 'just now';
    if (m < 60) return m + 'm ago';
    return Math.round(m / 60) + 'h ago';
  }

  function ChannelChip({ id, config }) {
    const m = CHANNEL_META[id];
    const label = id === 'email' ? ((config.email || {}).to || 'Email') : ((config[id] || {}).url ? m.name : m.name + ' (unconfigured)');
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '2px 8px', borderRadius: 6, background: 'var(--slate-100)', color: 'var(--slate-600)', fontSize: 11.5, fontWeight: 600 }}>
        <Icon name={m.icon} size={12} />{label}
      </span>
    );
  }

  function ChannelRow({ id, config, onSave, onTest, testResult }) {
    const meta = CHANNEL_META[id];
    const current = config[id] || {};
    const connected = id === 'email' ? !!current.smtp_host : !!current.url;
    const [editing, setEditing] = React.useState(false);
    const [draft, setDraft] = React.useState(current);
    const detail = id === 'email'
      ? (connected ? `${current.smtp_host}:${current.smtp_port || 1025} · ${current.to || '—'}` : 'SMTP delivery (e.g. Mailpit)')
      : (connected ? current.url : 'POST JSON to your endpoint');
    return (
      <div style={{ padding: '11px 0', borderTop: '1px solid var(--slate-100)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ width: 32, height: 32, flex: 'none', borderRadius: 8, background: 'var(--slate-100)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
            <Icon name={meta.icon} size={16} color="var(--slate-600)" />
          </span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', fontSize: 13.5, fontWeight: 600, color: 'var(--slate-900)' }}>{meta.name}</span>
            <span style={{ display: 'block', fontSize: 12, color: 'var(--slate-500)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{detail}</span>
          </span>
          {connected && <StatusPill tone="success" size="sm">Connected</StatusPill>}
          {connected && <Button variant="secondary" size="sm" onClick={() => onTest(id)}>Test</Button>}
          <Button variant="secondary" size="sm" onClick={() => { setDraft(current); setEditing(!editing); }}>{connected ? 'Edit' : 'Connect'}</Button>
        </div>
        {testResult && testResult.channel === id && (
          <div style={{ marginTop: 8, fontSize: 12, color: testResult.ok ? 'var(--green-700)' : 'var(--red-600)' }}>
            {testResult.ok ? `Delivered in ${testResult.attempts} attempt${testResult.attempts === 1 ? '' : 's'}.` : `Failed after ${testResult.attempts || 0} attempts: ${(testResult.errors || []).slice(-1)[0] || 'not configured'}`}
          </div>
        )}
        {editing && (
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {id === 'email' ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 0.5fr 1fr 1fr', gap: 8 }}>
                  <Input label="SMTP Host" value={draft.smtp_host || ''} onChange={(v) => setDraft({ ...draft, smtp_host: v })} placeholder="smtp.sendgrid.net" />
                  <Input label="Port" value={String(draft.smtp_port || 1025)} onChange={(v) => setDraft({ ...draft, smtp_port: parseInt(v, 10) || 1025 })} />
                  <Input label="To" value={draft.to || ''} onChange={(v) => setDraft({ ...draft, to: v })} placeholder="sre@acme.io" />
                  <Input label="From" value={draft.from || ''} onChange={(v) => setDraft({ ...draft, from: v })} placeholder="queuelens@acme.io" />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 8, alignItems: 'end' }}>
                  <Input label="SMTP Username (optional)" value={draft.username || ''} onChange={(v) => setDraft({ ...draft, username: v })} placeholder="apikey" />
                  <Input label="SMTP Password" type="password" value={draft.password || ''} onChange={(v) => setDraft({ ...draft, password: v })} placeholder={draft.password === '__secret__' ? 'saved — type to replace' : ''} />
                  <Switch checked={draft.use_tls != null ? !!draft.use_tls : !!draft.username} onChange={(v) => setDraft({ ...draft, use_tls: v })} label="STARTTLS" description="Port 465 uses implicit TLS automatically" />
                </div>
              </div>
            ) : (
              <Input label={meta.name + ' URL'} value={draft.url || ''} onChange={(v) => setDraft({ ...draft, url: v })} placeholder="https://…" />
            )}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              {connected && (
                <Button variant="danger" size="sm" onClick={() => { onSave(id, {}); setEditing(false); }}>Disconnect</Button>
              )}
              <div style={{ flex: 1 }} />
              <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
              <Button size="sm" onClick={() => { onSave(id, draft); setEditing(false); }}>Save</Button>
            </div>
          </div>
        )}
      </div>
    );
  }

  function Alerts({ nav }) {
    const [rules, setRules] = React.useState(() => window.QL.fetchAlerts());
    const [serverSettings, setServerSettings] = React.useState(() => window.QL.fetchServerSettings());
    const channels = serverSettings.channels || {};
    const ui = serverSettings.ui || {};
    const [building, setBuilding] = React.useState(false);
    const [busy, setBusy] = React.useState(false);
    const [error, setError] = React.useState(null);
    const [testResult, setTestResult] = React.useState(null);
    const emailConfigured = !!((serverSettings.channels || {}).email || {}).smtp_host;
    const [draft, setDraft] = React.useState({ name: '', pattern: '*.dlq', metric: 'Messages ready', op: '>', threshold: '100', dur: 'for 5 minutes', severity: 'Warning', email: emailConfigured, slack: false, webhook: false });
    const d = (k) => (v) => setDraft((s) => ({ ...s, [k]: v }));
    const reload = () => setRules(window.QL.fetchAlerts());

    const saveSettings = async (values) => {
      const next = await window.QL.saveSettings(values);
      setServerSettings(next);
    };
    const saveChannel = (id, config) => saveSettings({ channels: { ...channels, [id]: config } }).catch((e) => setError(e.message));
    const testChannel = async (id) => {
      setTestResult({ channel: id, pending: true });
      try {
        const outcome = await window.QL.postJson('/api/alerts/test-channel', { channel: id });
        setTestResult({ channel: id, ...outcome });
      } catch (e) { setTestResult({ channel: id, ok: false, errors: [e.message] }); }
    };

    const saveDraft = async () => {
      setBusy(true);
      setError(null);
      try {
        await window.QL.postJson('/api/alerts', {
          name: draft.name || 'Untitled rule',
          pattern: draft.pattern || '*',
          metric: METRIC_KEY[draft.metric] || 'messages_ready',
          operator: draft.op === '≥' ? '>=' : draft.op,
          threshold: parseInt(draft.threshold, 10) || 0,
          duration_seconds: DUR_KEY[draft.dur] ?? 0,
          severity: draft.severity,
          channels: ['email', 'slack', 'webhook'].filter((c) => draft[c]),
          enabled: true,
        });
        setBuilding(false);
        reload();
      } catch (e) { setError(e.message); } finally { setBusy(false); }
    };
    const toggleRule = (rule) =>
      window.QL.requestJson('PATCH', '/api/alerts/' + rule.id, { enabled: !rule.enabled }).then(reload).catch((e) => setError(e.message));
    const deleteRule = (rule) => {
      if (!window.confirm(`Delete rule "${rule.name}"?`)) return;
      window.QL.requestJson('DELETE', '/api/alerts/' + rule.id).then(reload).catch((e) => setError(e.message));
    };

    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageHeader title="Alerts" subtitle="Rules are evaluated on the server against live queue stats; firing rules notify and deliver to your channels."
            actions={<Button icon="plus" onClick={() => setBuilding(!building)}>New Alert Rule</Button>} />

          {error && <Alert tone="danger" style={{ marginBottom: 14 }}>{error}</Alert>}

          {building && (
            <Card title="New Alert Rule" subtitle="Notify when a condition holds on matching queues." style={{ marginBottom: 18 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 14, marginTop: 8 }}>
                <Input label="Rule Name" required placeholder="DLQ backlog critical" value={draft.name} onChange={d('name')} />
                <Input label="Queue Pattern" required value={draft.pattern} onChange={d('pattern')} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 0.6fr 0.8fr 1fr 1fr', gap: 14, marginTop: 14 }}>
                <Select label="Metric" options={Object.values(METRIC_LABEL)} value={draft.metric} onChange={d('metric')} />
                <Select label="Operator" options={['>', '>=', '=', '<']} value={draft.op} onChange={d('op')} />
                <Input label="Threshold" value={draft.threshold} onChange={d('threshold')} />
                <Select label="Duration" options={Object.values(DUR_LABEL)} value={draft.dur} onChange={d('dur')} />
                <Select label="Severity" options={['Info', 'Warning', 'Alert']} value={draft.severity} onChange={d('severity')} />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 24, marginTop: 16 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--slate-700)' }}>Send to <span style={{ fontWeight: 400, color: 'var(--slate-400)' }}>(optional — unchecked = in-app notification only)</span></span>
                <Checkbox checked={draft.email} onChange={d('email')} label={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><Icon name="mail" size={13} /> Email</span>} />
                <Checkbox checked={draft.slack} onChange={d('slack')} label={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><Icon name="hash" size={13} /> Slack</span>} />
                <Checkbox checked={draft.webhook} onChange={d('webhook')} label={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><Icon name="webhook" size={13} /> Webhook</span>} />
                <div style={{ flex: 1 }} />
                <Button variant="secondary" onClick={() => setBuilding(false)}>Cancel</Button>
                <Button onClick={saveDraft} disabled={busy}>{busy ? 'Saving…' : 'Save Rule'}</Button>
              </div>
            </Card>
          )}

          {rules.length === 0 && !building ? (
            <div style={{ padding: '40px 0' }}>
              <EmptyState icon="bell-ring" tone="neutral" title="No alert rules yet"
                actions={<Button icon="plus" onClick={() => setBuilding(true)}>Create your first rule</Button>}>
                Get notified before a DLQ becomes an incident — e.g. messages ready &gt; 100 on *.dlq.
              </EmptyState>
            </div>
          ) : (
            <Card pad={false}>
              <DataTable rowKey="id"
                columns={[
                  { key: 'name', label: 'Rule', render: (r) => <span style={{ fontWeight: 600, color: 'var(--slate-900)' }}>{r.name}</span> },
                  { key: 'cond', label: 'Condition', render: (r) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--slate-600)' }}>{r.pattern} · {r.metric} {r.operator} {r.threshold}{r.duration_seconds ? ` for ${Math.round(r.duration_seconds / 60)}m` : ''}</span> },
                  { key: 'severity', label: 'Severity', render: (r) => <StatusPill tone={SEV_TONE[r.severity]}>{r.severity}</StatusPill> },
                  { key: 'channels', label: 'Channels', render: (r) => <span style={{ display: 'inline-flex', gap: 5 }}>{r.channels.length ? r.channels.map((c) => <ChannelChip key={c} id={c} config={channels} />) : <span style={{ fontSize: 12, color: 'var(--slate-400)' }}>in-app only</span>}</span> },
                  { key: 'last', label: 'Last Triggered', render: (r) => rel(r.last_fired_at) },
                  { key: 'on', label: 'Enabled', align: 'right', render: (r) => <span onClick={(e) => e.stopPropagation()}><Switch checked={r.enabled} onChange={() => toggleRule(r)} /></span> },
                  { key: 'a', label: '', align: 'right', render: (r) => <IconButton icon="trash-2" size={28} onClick={() => deleteRule(r)} /> },
                ]}
                rows={rules} footer={`${rules.length} rules · evaluated on the server every ~15s · firing rules appear in Notifications`} />
            </Card>
          )}

          <Alert tone="info" style={{ marginTop: 18 }}>
            Firing rules create Notifications and deliver through the channels configured on the right
            (email retries 3× with backoff). Recovery notices are sent when the condition clears.
          </Alert>
        </div>

        <aside style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none', display: 'flex', flexDirection: 'column', gap: 16, position: 'sticky', top: 16 }}>
          <Card title="Delivery Channels" subtitle="Where alert notifications are sent. Saved on the server.">
            <div style={{ marginTop: 4 }}>
              {['email', 'slack', 'pagerduty', 'webhook'].map((id) => (
                <ChannelRow key={id} id={id} config={channels} onSave={saveChannel} onTest={testChannel} testResult={testResult} />
              ))}
            </div>
          </Card>
          <Card title="Quiet Hours" subtitle="Mute non-critical alerts on a schedule.">
            <div style={{ marginTop: 4 }}>
              <Switch checked={!!ui.quiet_hours} onChange={(v) => saveSettings({ ui: { ...ui, quiet_hours: v } }).catch((e) => setError(e.message))} label="Enable quiet hours" description="Alert-severity rules always deliver." />
              {!!ui.quiet_hours && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 14 }}>
                  <Input label="From" value={ui.quiet_from || '22:00'} onChange={(v) => saveSettings({ ui: { ...ui, quiet_from: v } })} />
                  <Input label="Until" value={ui.quiet_until || '07:00'} onChange={(v) => saveSettings({ ui: { ...ui, quiet_until: v } })} />
                </div>
              )}
            </div>
          </Card>
        </aside>
      </div>
    );
  }

  window.QL.screens.Alerts = Alerts;
})();
