// Users screen — accounts + role permission matrix.
(function () {
  const { Icon, StatusPill, Button, IconButton, DataTable } = window.__NS;
  const { PageHeader, Card } = window.QL;
  const D = window.QL.data;

  const ROLE_TONE = { Admin: 'park', Operator: 'info', Viewer: 'neutral' };
  const STATUS_TONE = { Active: 'success', Invited: 'warning', Service: 'neutral' };
  const CAPS = [
    { label: 'Browse queues & messages', viewer: true, operator: true, admin: true },
    { label: 'Replay messages', viewer: false, operator: true, admin: true },
    { label: 'Park messages', viewer: false, operator: true, admin: true },
    { label: 'Delete messages', viewer: false, operator: false, admin: true },
    { label: 'Manage configuration', viewer: false, operator: false, admin: true },
    { label: 'Manage users & roles', viewer: false, operator: false, admin: true },
  ];

  function Cap({ ok }) {
    return ok
      ? <Icon name="check" size={14} color="var(--green-600)" strokeWidth={2.5} />
      : <Icon name="minus" size={14} color="var(--slate-300)" />;
  }

  function mapAccounts() {
    var accounts = (window.QL.requestJson ? null : null);
    var raw = [];
    try {
      var x = new XMLHttpRequest();
      x.open('GET', '/api/users', false);
      x.send();
      raw = (JSON.parse(x.responseText).accounts) || [];
    } catch (e) { raw = []; }
    return raw.map(function (a) {
      return {
        name: a.username, email: a.email || '\u2014',
        role: a.role === 'Administrator' ? 'Admin' : a.role,
        envs: [(window.QL.broker || {}).environment || 'development'],
        last: a.invited_by ? 'invited by ' + a.invited_by : '\u2014',
        status: a.active === false ? 'Invited' : 'Active',
      };
    });
  }

  function PasswordCard() {
    const { Input, Button, Alert } = window.__NS;
    const [draft, setDraft] = React.useState({ current: '', next: '' });
    const [status, setStatus] = React.useState(null);
    const change = async () => {
      setStatus(null);
      try {
        await window.QL.postJson('/api/users/me/password', {
          current_password: draft.current, new_password: draft.next,
        });
        setStatus({ ok: true, text: 'Password changed. Use it on your next request.' });
        setDraft({ current: '', next: '' });
      } catch (e) { setStatus({ ok: false, text: e.message }); }
    };
    return (
      <Card title="My Password" subtitle={`Signed in as ${(window.QL.me || {}).username} (${(window.QL.me || {}).role})`}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
          <Input label="Current password" type="password" value={draft.current} onChange={(v) => setDraft({ ...draft, current: v })} />
          <Input label="New password (10+ chars)" type="password" value={draft.next} onChange={(v) => setDraft({ ...draft, next: v })} />
          <Button size="sm" disabled={!draft.current || draft.next.length < 10} onClick={change}>Change Password</Button>
          {status && <Alert tone={status.ok ? 'success' : 'danger'}>{status.text}</Alert>}
        </div>
      </Card>
    );
  }

  function Users({ nav }) {
    const [users, setUsers] = React.useState(mapAccounts);
    const [inviting, setInviting] = React.useState(false);
    const [draft, setDraft] = React.useState({ username: '', role: 'Operator', email: '' });
    const [result, setResult] = React.useState(null);
    const [error, setError] = React.useState(null);
    const { Input, Select, Alert, CodeBlock } = window.__NS;
    const invite = async () => {
      setError(null);
      try {
        const outcome = await window.QL.postJson('/api/users/invite', {
          username: draft.username.trim(), role: draft.role, email: draft.email.trim() || null,
        });
        setResult(outcome);
        setInviting(false);
        setUsers(mapAccounts());
      } catch (e) { setError(e.message); }
    };
    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageHeader title="Users" subtitle="Manage who can browse, recover, and configure QueueLens."
            actions={(window.QL.me || {}).role === 'Admin' ? <Button icon="user-plus" onClick={() => { setResult(null); setInviting(!inviting); }}>Invite User</Button> : null} />
          {error && <Alert tone="danger" style={{ marginBottom: 14 }}>{error}</Alert>}
          {inviting && (
            <Card title="Invite User" subtitle="Creates a real account (stored server-side). The password is shown exactly once." style={{ marginBottom: 18 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 0.7fr 1fr auto', gap: 12, alignItems: 'end', marginTop: 4 }}>
                <Input label="Username" required placeholder="new.sre" value={draft.username} onChange={(v) => setDraft({ ...draft, username: v })} />
                <Select label="Role" options={['Operator', 'Admin', 'Viewer']} value={draft.role} onChange={(v) => setDraft({ ...draft, role: v })} />
                <Input label="Email (optional — invite is emailed if a channel is configured)" placeholder="person@acme.io" value={draft.email} onChange={(v) => setDraft({ ...draft, email: v })} />
                <Button style={{ height: 38 }} disabled={!draft.username.trim()} onClick={invite}>Send Invite</Button>
              </div>
            </Card>
          )}
          {result && (
            <Alert tone="success" title={`Invited ${result.username} as ${result.role}`} style={{ marginBottom: 18 }}>
              One-time password (copy it now — it is not stored in plain text):
              <div style={{ marginTop: 8 }}><CodeBlock code={result.password} copy /></div>
              {result.email_delivery && (
                <div style={{ marginTop: 8, fontSize: 12.5 }}>
                  Invite email: {result.email_delivery.ok ? `delivered (attempt ${result.email_delivery.attempts})` : `failed after ${result.email_delivery.attempts} attempts`}
                </div>
              )}
            </Alert>
          )}
          <Card pad={false}>
            <DataTable rowKey="name"
              columns={[
                { key: 'name', label: 'User', render: (r) => (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ width: 30, height: 30, borderRadius: 999, background: 'var(--slate-200)', color: 'var(--slate-600)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, flex: 'none' }}>
                      {r.name.slice(0, 2).toUpperCase()}
                    </span>
                    <span>
                      <span style={{ display: 'block', fontWeight: 600, color: 'var(--slate-900)' }}>{r.name}</span>
                      <span style={{ display: 'block', fontSize: 12, color: 'var(--slate-500)' }}>{r.email}</span>
                    </span>
                  </span>) },
                { key: 'role', label: 'Role', render: (r) => <StatusPill tone={ROLE_TONE[r.role]}>{r.role}</StatusPill> },
                { key: 'envs', label: 'Environments', render: (r) => (
                  <span style={{ display: 'inline-flex', gap: 5 }}>
                    {r.envs.map((e) => <span key={e} style={{ padding: '2px 7px', borderRadius: 6, background: 'var(--slate-100)', color: 'var(--slate-600)', fontSize: 11.5, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{e}</span>)}
                  </span>) },
                { key: 'last', label: 'Last Active' },
                { key: 'status', label: 'Status', render: (r) => <StatusPill tone={STATUS_TONE[r.status]}>{r.status}</StatusPill> },
                { key: 'a', label: '', align: 'right', render: () => <IconButton icon="ellipsis-vertical" size={28} /> },
              ]}
              rows={users} footer={`${users.length} users`} />
          </Card>
        </div>

        <aside style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none', position: 'sticky', top: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <PasswordCard />
          <Card title="Roles" subtitle="What each role is allowed to do.">
            <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-ui)', marginTop: 4 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', fontSize: 12, fontWeight: 600, color: 'var(--slate-500)', padding: '6px 0' }}></th>
                  {['Viewer', 'Operator', 'Admin'].map((r) => (
                    <th key={r} style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--slate-500)', padding: '6px 4px', textAlign: 'center' }}>{r}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {CAPS.map((c) => (
                  <tr key={c.label}>
                    <td style={{ fontSize: 12.5, color: 'var(--slate-600)', padding: '7px 8px 7px 0', borderTop: '1px solid var(--slate-100)' }}>{c.label}</td>
                    <td style={{ textAlign: 'center', borderTop: '1px solid var(--slate-100)' }}><Cap ok={c.viewer} /></td>
                    <td style={{ textAlign: 'center', borderTop: '1px solid var(--slate-100)' }}><Cap ok={c.operator} /></td>
                    <td style={{ textAlign: 'center', borderTop: '1px solid var(--slate-100)' }}><Cap ok={c.admin} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginTop: 14, padding: '10px 12px', background: 'var(--blue-50)', border: '1px solid var(--blue-200)', borderRadius: 'var(--radius-md)' }}>
              <Icon name="shield-check" size={15} color="var(--blue-600)" style={{ marginTop: 1 }} />
              <span style={{ fontSize: 12.5, color: 'var(--slate-600)', lineHeight: 1.5 }}>Only Admins can delete messages. Operators recover with non-destructive actions; Viewers browse read-only.</span>
            </div>
          </Card>
        </aside>
      </div>
    );
  }

  window.QL.screens.Users = Users;
})();
