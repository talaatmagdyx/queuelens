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

  function Users({ nav }) {
    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageHeader title="Users" subtitle="Manage who can browse, recover, and configure QueueLens."
            actions={<Button icon="user-plus">Invite User</Button>} />
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
              rows={D.users} footer={`${D.users.length} users`} />
          </Card>
        </div>

        <aside style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none', position: 'sticky', top: 16 }}>
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
