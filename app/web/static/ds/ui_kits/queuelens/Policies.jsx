// Replay Policies screen: DLQs QueueLens retries by itself (/api/policies). A run replays
// due messages to the queue they died in, with backoff, and parks the exhausted ones.
// A policy runs in the environment and vhost it was created in, whatever this tab shows.
(function () {
  const { Icon, StatusPill, Button, IconButton, DataTable, Input, Select, Switch, Alert } = window.__NS;
  const { PageHeader, Card } = window.QL;
  const D = window.QL.data;

  function rel(iso) {
    if (!iso) return 'never';
    const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
    if (isNaN(m)) return '—';
    if (m < 1) return 'just now';
    if (m < 60) return m + 'm ago';
    return Math.round(m / 60) + 'h ago';
  }

  function lastRun(p) {
    const r = p.last_result || {};
    if (!p.last_run_at) return 'never';
    const parts = [];
    if (r.replayed) parts.push(r.replayed + ' replayed');
    if (r.parked) parts.push(r.parked + ' parked');
    if (r.failed) parts.push(r.failed + ' failed');
    if (r.skipped_no_consumers) parts.push(r.skipped_no_consumers + ' held: no consumers');
    if (r.waiting) parts.push(r.waiting + ' waiting');
    return rel(p.last_run_at) + (parts.length ? ' · ' + parts.join(', ') : ' · nothing due');
  }

  const fetchPolicies = () => {
    try {
      const x = new XMLHttpRequest();
      x.open('GET', '/api/policies', false);
      x.send();
      return x.status === 200 ? JSON.parse(x.responseText).policies : [];
    } catch (e) { return []; }
  };

  const EMPTY = { name: '', queue: '', max_deaths: '3', backoff_minutes: '5', interval_minutes: '10', cap: '100' };

  function Policies({ nav }) {
    const me = window.QL.me || {};
    const isAdmin = me.role === 'Admin';
    const canAct = me.role !== 'Viewer';
    const [policies, setPolicies] = React.useState(fetchPolicies);
    const [editing, setEditing] = React.useState(null); // null, 'new', or a policy id
    const [draft, setDraft] = React.useState(EMPTY);
    const [error, setError] = React.useState(null);
    const [note, setNote] = React.useState(null);
    const here = window.QL.broker || {};
    const where = (p) => p.environment + ' · ' + p.vhost;
    const current = policies.find((p) => p.id === editing);
    // this tab's DLQs only belong to a policy of this tab's scope
    const sameScope = !current || (current.environment === here.environment && current.vhost === here.vhost);
    const dlqs = sameScope ? D.queues.filter((q) => q.type === 'DLQ').map((q) => q.name) : [];
    const reload = () => setPolicies(fetchPolicies());
    const call = async (method, path, body) => {
      setError(null);
      try { return await window.QL.requestJson(method, path, body); } catch (e) { setError(e.message); return null; }
    };
    const open = (p) => {
      setNote(null);
      setEditing(p ? p.id : 'new');
      setDraft(p ? { name: p.name, queue: p.queue, max_deaths: String(p.max_deaths), backoff_minutes: String(p.backoff_minutes), interval_minutes: String(p.interval_minutes), cap: String(p.cap) } : { ...EMPTY, queue: dlqs[0] || '' });
    };
    const save = async () => {
      const body = { name: draft.name.trim(), queue: draft.queue, max_deaths: +draft.max_deaths, backoff_minutes: +draft.backoff_minutes, interval_minutes: +draft.interval_minutes, cap: +draft.cap };
      const saved = editing === 'new' ? await call('POST', '/api/policies', body) : await call('PUT', '/api/policies/' + editing, body);
      if (saved) { setEditing(null); reload(); }
    };
    const toggle = async (p) => { if (await call('PATCH', '/api/policies/' + p.id, { enabled: !p.enabled })) reload(); };
    const remove = async (p) => {
      if (!window.confirm(`Delete the replay policy ${p.name}? Messages in ${p.queue} stay where they are.`)) return;
      if (await call('DELETE', '/api/policies/' + p.id)) reload();
    };
    const preview = async (p) => {
      const r = await call('POST', '/api/policies/' + p.id + '/preview');
      if (!r) return;
      const targets = Object.entries(r.targets || {}).map(([t, v]) => `${v.due} → ${t}${v.consumers === 0 ? ' (no consumers: held)' : ''}`);
      setNote({ tone: 'info', title: `If ${p.name} ran now`, text: [targets.join(', ') || 'nothing due to replay', `${r.to_park || 0} to park`, `${r.waiting} waiting for their backoff`, r.no_history || r.no_target ? `${r.no_history + r.no_target} skipped (no history or nowhere to replay)` : null, r.capped ? `${r.capped} over the cap, next run` : null].filter(Boolean).join(' · ') });
    };
    const runNow = async (p) => {
      if (!window.confirm(`Run ${p.name} now? Due messages in ${p.queue} are replayed and exhausted ones parked.`)) return;
      const r = await call('POST', '/api/policies/' + p.id + '/run');
      if (r) { setNote({ tone: r.failed ? 'warning' : 'success', title: `${p.name} ran`, text: `${r.replayed} replayed, ${r.parked} parked, ${r.failed} failed, ${r.skipped_no_consumers} held (no consumers), ${r.waiting} waiting.` }); reload(); }
    };
    const field = (key, label, suffix) => <Input label={label} value={draft[key]} suffix={suffix} onChange={(v) => setDraft({ ...draft, [key]: v })} />;

    return (
      <div>
        <PageHeader title="Replay Policies" subtitle="DLQs QueueLens retries by itself: due messages go back to the queue they died in, with backoff; exhausted ones are parked."
          actions={isAdmin ? <Button icon="plus" onClick={() => open(null)}>New Policy</Button> : null} />
        {error && <Alert tone="danger" style={{ marginBottom: 14 }}>{error}</Alert>}
        {note && <Alert tone={note.tone} title={note.title} style={{ marginBottom: 14 }}>{note.text}</Alert>}
        {editing && (
          <Card title={editing === 'new' ? 'New Replay Policy' : 'Edit Replay Policy'} subtitle={'Runs in ' + (current ? where(current) : where(here)) + (current ? '' : ' (this tab)') + ', on the replica that leads the alert engine.'} style={{ marginBottom: 18 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 12, marginTop: 4 }}>
              {field('name', 'Name')}
              <Select label="Dead-letter queue" options={dlqs.length ? dlqs : [draft.queue]} value={draft.queue} onChange={(v) => setDraft({ ...draft, queue: v })} />
              {field('max_deaths', 'Park at', 'deaths')}
              {field('backoff_minutes', 'Backoff', 'min × 2ⁿ⁻¹')}
              {field('interval_minutes', 'Run every', 'min')}
              {field('cap', 'At most', 'per run')}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
              <Button variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
              <Button disabled={!draft.name.trim() || !draft.queue} onClick={save}>Save</Button>
            </div>
          </Card>
        )}
        <Card pad={false}>
          <DataTable rowKey="id"
            columns={[
              { key: 'name', label: 'Policy', render: (p) => <span style={{ fontWeight: 600, color: 'var(--slate-900)', whiteSpace: 'normal' }}>{p.name}</span> },
              { key: 'queue', label: 'DLQ', render: (p) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, whiteSpace: 'normal', overflowWrap: 'break-word' }}>{p.queue}</span> },
              { key: 'where', label: 'Runs In', render: (p) => <span style={{ fontSize: 12.5, color: 'var(--slate-600)', whiteSpace: 'normal' }}>{where(p)}</span> },
              { key: 'rule', label: 'Rule', render: (p) => <span style={{ fontSize: 12.5, color: 'var(--slate-600)', whiteSpace: 'normal' }}>backoff {p.backoff_minutes}m × 2ⁿ⁻¹ · park at {p.max_deaths} · every {p.interval_minutes}m · ≤ {p.cap}</span> },
              { key: 'last', label: 'Last Run', render: (p) => <span style={{ fontSize: 12.5, whiteSpace: 'normal' }}>{lastRun(p)}{p.consecutive_failures ? <StatusPill tone="danger" style={{ marginLeft: 6 }}>{p.consecutive_failures} failed in a row</StatusPill> : null}</span> },
              { key: 'on', label: 'Enabled', align: 'right', render: (p) => <span onClick={(e) => e.stopPropagation()} title={!isAdmin && !p.enabled ? 'Only an Admin can turn a policy back on' : ''}><Switch checked={p.enabled} onChange={() => (canAct && (p.enabled || isAdmin)) && toggle(p)} /></span> },
              { key: 'a', label: '', align: 'right', render: (p) => (
                <span style={{ display: 'inline-flex', gap: 6, whiteSpace: 'nowrap' }}>
                  <IconButton icon="history" size={28} title="History: this policy's runs and moves, in the Audit Log" onClick={() => nav('audit', { user: 'policy:' + p.name })} />
                  {canAct && <IconButton icon="eye" size={28} title="Preview: what a run would do now" onClick={() => preview(p)} />}
                  {isAdmin && <IconButton icon="play" size={28} title="Run now" onClick={() => runNow(p)} />}
                  {isAdmin && <IconButton icon="pencil" size={28} title="Edit" onClick={() => open(p)} />}
                  {isAdmin && <IconButton icon="trash-2" size={28} title="Delete" onClick={() => remove(p)} />}
                </span>) },
            ]}
            rows={policies} footer={policies.length ? `${policies.length} ${policies.length === 1 ? 'policy' : 'policies'} · checked every 30s` : 'No replay policies yet'} />
        </Card>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginTop: 16, padding: '10px 12px', background: 'var(--blue-50)', border: '1px solid var(--blue-200)', borderRadius: 'var(--radius-md)' }}>
          <Icon name="shield-check" size={15} color="var(--blue-600)" style={{ marginTop: 1 }} />
          <span style={{ fontSize: 12.5, color: 'var(--slate-600)', lineHeight: 1.5 }}>
            A policy runs in the environment and vhost it was created in. A run goes through the same path as a bulk action: dry run, publish before ack, and an audit row per message as <code>policy:&lt;name&gt;</code>.
            It holds back a replay when the target queue has no consumers, and pauses the policy after 3 failed runs in a row, notifying the alert channels.
          </span>
        </div>
      </div>
    );
  }

  window.QL.screens.Policies = Policies;
})();
