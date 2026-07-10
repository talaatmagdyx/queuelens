// Notifications page + bell popover.
(function () {
  const { Icon, StatusPill, Button, IconButton, DataTable, Tabs, Pagination, Select } = window.__NS;
  const { PageHeader, Card, ArrowLink } = window.QL;
  const D = window.QL.data;

  const LEVEL_TONE = { Alert: 'danger', Warning: 'warning', Info: 'info', Success: 'success' };
  const LEVEL_ICON = { Alert: { n: 'alert-triangle', c: 'var(--red-600)' }, Warning: { n: 'alert-triangle', c: 'var(--amber-600)' }, Info: { n: 'info', c: 'var(--blue-600)' }, Success: { n: 'check-circle', c: 'var(--green-600)' } };

  const SOURCE_ROUTE = { 'Queue Monitor': 'queues', 'Audit Log': 'audit' };

  function Notifications({ nav }) {
    const [tab, setTab] = React.useState('all');
    const [page, setPage] = React.useState(1);
    const PAGE_SIZE = 10;
    const all = D.notifications;
    const countOf = (level) => all.filter((n) => n.level === level).length;
    const rows = all.filter((n) => tab === 'all' || n.level.toLowerCase() === tab || (tab === 'alerts' && n.level === 'Alert'));
    const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    const safePage = Math.min(page, pageCount);
    const pageRows = rows.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);
    return (
      <div>
        <PageHeader title="Notifications" subtitle="Stay updated on important events and system activities." />
        <Card pad={false}>
          <div style={{ padding: '14px 20px 0' }}>
            <Tabs active={tab} onChange={(t) => { setTab(t); setPage(1); }} tabs={[
              { id: 'all', label: 'All', count: all.length }, { id: 'alerts', label: 'Alerts', count: countOf('Alert') },
              { id: 'warning', label: 'Warnings', count: countOf('Warning') }, { id: 'info', label: 'Info', count: countOf('Info') },
              { id: 'success', label: 'Success', count: countOf('Success') }]} />
          </div>
          <DataTable rowKey="time"
            columns={[
              { key: 'time', label: 'Time' },
              { key: 'level', label: 'Level', render: (r) => <StatusPill tone={LEVEL_TONE[r.level]}>{r.level}</StatusPill> },
              { key: 'title', label: 'Title', render: (r) => <span style={{ fontWeight: 600, color: 'var(--slate-900)' }}>{r.title}</span> },
              { key: 'message', label: 'Message' },
              { key: 'source', label: 'Source' },
              { key: 'a', label: 'Actions', align: 'right', render: (r) => <Button variant="secondary" size="sm" style={{ color: 'var(--text-link)' }} onClick={() => nav(SOURCE_ROUTE[r.source] || 'dashboard')}>View</Button> },
            ]}
            rows={pageRows} />
          <div style={{ display: 'flex', alignItems: 'center', padding: '14px 20px', borderTop: '1px solid var(--slate-100)' }}>
            <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
              {rows.length ? `Showing ${(safePage - 1) * PAGE_SIZE + 1} to ${Math.min(safePage * PAGE_SIZE, rows.length)} of ${rows.length} notifications` : 'No notifications'}
            </span>
            <div style={{ flex: 1 }} />
            <Pagination page={safePage} pageCount={pageCount} onChange={setPage} />
          </div>
        </Card>
      </div>
    );
  }

  // Bell dropdown
  function NotificationsPopover({ onViewAll, onClose }) {
    return (
      <div style={{ position: 'absolute', top: 52, right: 16, width: 360, background: 'var(--surface-raised)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-popover)', zIndex: 50, fontFamily: 'var(--font-ui)' }}>
        <div style={{ display: 'flex', alignItems: 'center', padding: '14px 16px', borderBottom: '1px solid var(--slate-100)' }}>
          <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-heading)', flex: 1 }}>Notifications</span>
        </div>
        {D.notifications.slice(0, 3).map((n) => {
          const ic = LEVEL_ICON[n.level];
          return (
            <div key={n.time} style={{ display: 'flex', gap: 12, padding: '13px 16px', borderBottom: '1px solid var(--slate-100)' }}>
              <div style={{ width: 30, height: 30, flex: 'none', borderRadius: 8, background: n.level === 'Alert' ? 'var(--red-100)' : n.level === 'Warning' ? 'var(--amber-100)' : 'var(--blue-50)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Icon name={ic.n} size={15} color={ic.c} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--slate-900)' }}>{n.title}</div>
                <div style={{ fontSize: 12.5, color: 'var(--slate-500)', marginTop: 2 }}>{n.message}</div>
              </div>
              <span style={{ fontSize: 12, color: 'var(--slate-400)', flex: 'none' }}>{n.time}</span>
            </div>
          );
        })}
        <div style={{ padding: '12px 16px', textAlign: 'center' }}>
          <a href="#" onClick={(e) => { e.preventDefault(); onViewAll(); }} style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-link)', textDecoration: 'none' }}>View all notifications</a>
        </div>
      </div>
    );
  }

  window.QL.screens.Notifications = Notifications;
  window.QL.NotificationsPopover = NotificationsPopover;
})();
