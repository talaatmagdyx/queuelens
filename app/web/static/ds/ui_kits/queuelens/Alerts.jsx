// Alerts screen — alert rules, rule builder, delivery channels, quiet hours.
// Full design version. Rules and quiet-hours are UI-level settings persisted in
// localStorage; the packaged Prometheus rules (/api/alert-rules) seed the list.
(function () {
  const { Icon, StatusPill, Button, IconButton, DataTable, Input, Select, Switch, Checkbox, Alert } = window.__NS;
  const { PageHeader, Card } = window.QL;

  const SEV_TONE = { Alert: 'danger', Warning: 'warning', Info: 'info' };
  const PROM_SEV = { critical: 'Alert', warning: 'Warning', info: 'Info' };

  function seedRules() {
    try {
      const stored = localStorage.getItem('ql_alert_rules');
      if (stored) return JSON.parse(stored);
    } catch (e) {}
    // Seed from the packaged Prometheus rules so the list starts real.
    const packaged = window.QL.fetchAlertRules().rules.map((r, i) => ({
      id: i + 1, name: r.name, cond: r.expr + ' for ' + r.for,
      severity: PROM_SEV[r.severity] || 'Warning', channels: ['email'],
      env: (window.QL.broker || {}).environment || 'all', on: true, last: '—',
    }));
    return packaged.length ? packaged : [
      { id: 1, name: 'DLQ backlog critical', cond: 'payments.* · messages ready > 100 for 5m', severity: 'Alert', channels: ['slack', 'email'], env: 'production', on: true, last: '—' },
    ];
  }

  const CHANNEL_META = {
    slack: { icon: 'hash', label: '#queue-alerts' },
    email: { icon: 'mail', label: 'sre@acme.io' },
    pagerduty: { icon: 'phone-call', label: 'PagerDuty' },
    webhook: { icon: 'webhook', label: 'Webhook' },
  };

  function ChannelChip({ id }) {
    const m = CHANNEL_META[id];
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '2px 8px', borderRadius: 6, background: 'var(--slate-100)', color: 'var(--slate-600)', fontSize: 11.5, fontWeight: 600 }}>
        <Icon name={m.icon} size={12} />{m.label}
      </span>
    );
  }

  function ChannelRow({ icon, name, detail, connected }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 0', borderTop: '1px solid var(--slate-100)' }}>
        <span style={{ width: 32, height: 32, flex: 'none', borderRadius: 8, background: 'var(--slate-100)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
          <Icon name={icon} size={16} color="var(--slate-600)" />
        </span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 13.5, fontWeight: 600, color: 'var(--slate-900)' }}>{name}</span>
          <span style={{ display: 'block', fontSize: 12, color: 'var(--slate-500)' }}>{detail}</span>
        </span>
        {connected
          ? <StatusPill tone="success" size="sm">Connected</StatusPill>
          : <Button variant="secondary" size="sm">Connect</Button>}
      </div>
    );
  }

  function Alerts({ nav }) {
    const [rules, setRulesState] = React.useState(seedRules);
    const setRules = (fn) => setRulesState((rs) => {
      const next = typeof fn === 'function' ? fn(rs) : fn;
      try { localStorage.setItem('ql_alert_rules', JSON.stringify(next)); } catch (e) {}
      return next;
    });
    const [building, setBuilding] = React.useState(false);
    const [draft, setDraft] = React.useState({ name: '', pattern: '*.dlq', metric: 'Messages ready', op: '>', threshold: '100', dur: 'for 5 minutes', severity: 'Warning', slack: true, email: false });
    const [quiet, setQuietState] = React.useState(localStorage.getItem('ql_quiet_hours') !== 'off');
    const setQuiet = (v) => { setQuietState(v); localStorage.setItem('ql_quiet_hours', v ? 'on' : 'off'); };
    const d = (k) => (v) => setDraft((s) => ({ ...s, [k]: v }));
    const toggleRule = (id) => setRules((rs) => rs.map((r) => r.id === id ? { ...r, on: !r.on } : r));
    const saveDraft = () => {
      const channels = [draft.slack && 'slack', draft.email && 'email'].filter(Boolean);
      setRules((rs) => [{
        id: Date.now(), name: draft.name || 'Untitled rule',
        cond: `${draft.pattern} · ${draft.metric.toLowerCase()} ${draft.op} ${draft.threshold} ${draft.dur.replace('for ', 'for ').replace(' minutes', 'm')}`,
        severity: draft.severity, channels: channels.length ? channels : ['email'], env: 'all', on: true, last: '—',
      }, ...rs]);
      setBuilding(false);
    };

    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageHeader title="Alerts" subtitle="Get notified before a DLQ becomes an incident. Rules are evaluated every refresh interval."
            actions={<Button icon="plus" onClick={() => setBuilding(!building)}>New Alert Rule</Button>} />

          {building && (
            <Card title="New Alert Rule" subtitle="Notify when a condition holds on matching queues." style={{ marginBottom: 18 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 14, marginTop: 8 }}>
                <Input label="Rule Name" required placeholder="DLQ backlog critical" value={draft.name} onChange={d('name')} />
                <Input label="Queue Pattern" required value={draft.pattern} onChange={d('pattern')} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 0.6fr 0.8fr 1fr 1fr', gap: 14, marginTop: 14 }}>
                <Select label="Metric" options={['Messages ready', 'Oldest message age', 'Consumers', 'Failed actions', 'Message rate (in)']} value={draft.metric} onChange={d('metric')} />
                <Select label="Operator" options={['>', '≥', '=', '<']} value={draft.op} onChange={d('op')} />
                <Input label="Threshold" value={draft.threshold} onChange={d('threshold')} />
                <Select label="Duration" options={['immediately', 'for 5 minutes', 'for 15 minutes', 'for 1 hour']} value={draft.dur} onChange={d('dur')} />
                <Select label="Severity" options={['Info', 'Warning', 'Alert']} value={draft.severity} onChange={d('severity')} />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 24, marginTop: 16 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--slate-700)' }}>Send to</span>
                <Checkbox checked={draft.slack} onChange={d('slack')} label={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><Icon name="hash" size={13} /> Slack · #queue-alerts</span>} />
                <Checkbox checked={draft.email} onChange={d('email')} label={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><Icon name="mail" size={13} /> Email · sre@acme.io</span>} />
                <div style={{ flex: 1 }} />
                <Button variant="secondary" onClick={() => setBuilding(false)}>Cancel</Button>
                <Button onClick={saveDraft}>Save Rule</Button>
              </div>
            </Card>
          )}

          <Card pad={false}>
            <DataTable rowKey="id"
              columns={[
                { key: 'name', label: 'Rule', render: (r) => <span style={{ fontWeight: 600, color: 'var(--slate-900)' }}>{r.name}</span> },
                { key: 'cond', label: 'Condition', render: (r) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--slate-600)', whiteSpace: 'normal', display: 'inline-block', maxWidth: 320 }}>{r.cond}</span> },
                { key: 'severity', label: 'Severity', render: (r) => <StatusPill tone={SEV_TONE[r.severity]}>{r.severity}</StatusPill> },
                { key: 'channels', label: 'Channels', render: (r) => <span style={{ display: 'inline-flex', gap: 5 }}>{r.channels.map((c) => <ChannelChip key={c} id={c} />)}</span> },
                { key: 'env', label: 'Environment', render: (r) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{r.env}</span> },
                { key: 'last', label: 'Last Triggered' },
                { key: 'on', label: 'Enabled', align: 'right', render: (r) => <span onClick={(e) => e.stopPropagation()}><Switch checked={r.on} onChange={() => toggleRule(r.id)} /></span> },
                { key: 'a', label: '', align: 'right', render: () => <IconButton icon="ellipsis-vertical" size={28} /> },
              ]}
              rows={rules} footer={`${rules.length} rules · evaluated every refresh interval`} />
          </Card>

          <Alert tone="info" style={{ marginTop: 18 }}>
            Firing alerts also appear in Notifications. Resolved conditions send a recovery notice to the same channels.
            Delivery runs through Prometheus/Alertmanager scraping <span style={{ fontFamily: 'var(--font-mono)' }}>/metrics</span>.
          </Alert>
        </div>

        <aside style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none', display: 'flex', flexDirection: 'column', gap: 16, position: 'sticky', top: 16 }}>
          <Card title="Delivery Channels" subtitle="Where alert notifications are sent.">
            <div style={{ marginTop: 4 }}>
              <ChannelRow icon="hash" name="Slack" detail="acme.slack.com · #queue-alerts" connected />
              <ChannelRow icon="mail" name="Email" detail="smtp.acme.io · sre@acme.io" connected />
              <ChannelRow icon="phone-call" name="PagerDuty" detail="Escalate Alert-severity rules" />
              <ChannelRow icon="webhook" name="Webhook" detail="POST JSON to your endpoint" />
            </div>
          </Card>
          <Card title="Quiet Hours" subtitle="Mute non-critical alerts on a schedule.">
            <div style={{ marginTop: 4 }}>
              <Switch checked={quiet} onChange={setQuiet} label="Enable quiet hours" description="Alert-severity rules always deliver." />
              {quiet && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 14 }}>
                  <Input label="From" defaultValue="22:00" />
                  <Input label="Until" defaultValue="07:00" />
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
