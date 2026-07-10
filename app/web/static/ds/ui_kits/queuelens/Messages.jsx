// Messages screen with right detail panel.
(function () {
  const { Icon, Badge, StatusPill, Button, IconButton, Alert, DataTable, Tabs, Select, SearchInput, Checkbox, Pagination, KeyValue, CodeBlock } = window.__NS;
  const { PageHeader, Card, Breadcrumbs, XDeathTable, PAYLOAD_TONE } = window.QL;
  const D = window.QL.data;

  function Stat({ label, value, unit, info }) {
    return (
      <div style={{ flex: 1, padding: '16px 20px' }}>
        <div style={{ fontSize: 13, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 5 }}>{label}{info && <Icon name="info" size={13} color="var(--slate-400)" />}</div>
        <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-heading)', marginTop: 4 }}>
          {value}{unit && <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--slate-500)', marginLeft: 3 }}>{unit}</span>}
        </div>
      </div>
    );
  }

  const JOURNEY = [
    { t: '10:24:15', label: 'Landed in payments.retry.dlq', icon: 'alert-triangle', color: 'var(--red-600)', sub: 'x-death: maxlen (email.retry)' },
    { t: '10:22:40', label: 'Rejected by consumer', icon: 'x-circle', color: 'var(--red-600)', sub: 'email.processed · requeue=false' },
    { t: '10:22:31', label: 'TTL expired in email.retry', icon: 'clock', color: 'var(--amber-600)', sub: 'ttl 30s' },
    { t: '10:21:12', label: 'Published to email.exchange', icon: 'send', color: 'var(--blue-600)', sub: 'rk email.processed · orders-api' },
  ];

  function Journey() {
    return (
      <div style={{ padding: '4px 0 0 4px' }}>
        {JOURNEY.map((e, i) => (
          <div key={i} style={{ display: 'flex', gap: 12, position: 'relative', paddingBottom: i === JOURNEY.length - 1 ? 0 : 18 }}>
            {i < JOURNEY.length - 1 && <span style={{ position: 'absolute', left: 11, top: 24, bottom: 0, width: 2, background: 'var(--slate-100)' }}></span>}
            <span style={{ width: 24, height: 24, flex: 'none', borderRadius: 999, background: 'var(--surface-card)', border: '2px solid var(--slate-200)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1 }}>
              <Icon name={e.icon} size={12} color={e.color} />
            </span>
            <span style={{ minWidth: 0 }}>
              <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: 'var(--slate-900)' }}>{e.label}</span>
              <span style={{ display: 'block', fontSize: 11.5, fontFamily: 'var(--font-mono)', color: 'var(--slate-500)', marginTop: 2 }}>{e.t} · {e.sub}</span>
            </span>
          </div>
        ))}
      </div>
    );
  }

  function Messages({ nav, queue = 'payments.retry.dlq', role = 'Admin' }) {
    const canDelete = role === 'Admin';
    const canAct = role !== 'Viewer';
    const [rows, setRows] = React.useState(D.messages);
    const [selected, setSelected] = React.useState(D.messages[0].id);
    const [panelOpen, setPanelOpen] = React.useState(true);
    const [tab, setTab] = React.useState('payload');
    const [checked, setChecked] = React.useState([]);
    const [confirmDelete, setConfirmDelete] = React.useState(false);
    const [view, setView] = React.useState(null);
    const [deletedNote, setDeletedNote] = React.useState(0);
    const msg = rows.find((m) => m.id === selected) || rows[0] || D.messages[0];
    const allChecked = rows.length > 0 && checked.length === rows.length;
    const toggle = (id) => { setConfirmDelete(false); setChecked((c) => c.includes(id) ? c.filter((x) => x !== id) : [...c, id]); };
    const toggleAll = () => { setConfirmDelete(false); setChecked(allChecked ? [] : rows.map((r) => r.id)); };
    const clearSel = () => { setChecked([]); setConfirmDelete(false); };
    const bulkNav = (mode) => nav('replay', { msg: rows.find((r) => r.id === checked[0]) || msg, mode, count: checked.length });
    const doDelete = () => {
      const del = rows.filter((r) => checked.includes(r.id));
      window.QL.trash = [
        ...del.map((r) => ({ id: r.id, source: queue, by: 'admin', at: 'Just now', expires: '24h 0m', size: r.size, type: r.type })),
        ...(window.QL.trash || []),
      ];
      setRows((rs) => rs.filter((r) => !checked.includes(r.id)));
      setDeletedNote(del.length);
      clearSel();
    };
    const VIEWS = { 'x-death ≥ 3': (r) => r.xdeath >= 3, 'BASE64 payloads': (r) => r.type === 'BASE64', 'JSON only': (r) => r.type === 'JSON' };
    const visibleRows = view ? rows.filter(VIEWS[view]) : rows;
    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Breadcrumbs items={[
            { label: 'Queues', onClick: () => nav('queues') },
            { label: queue, onClick: () => nav('queues') },
            { label: 'Messages' }]} />
          <PageHeader title="Messages"
            after={<span style={{ display: 'inline-flex', gap: 8 }}>
              <span style={{ padding: '5px 12px', borderRadius: 8, background: 'var(--slate-100)', fontSize: 13.5, fontWeight: 600, color: 'var(--slate-700)' }}>{queue}</span>
              <StatusPill tone="danger" dot>DLQ · retry</StatusPill>
            </span>}
            subtitle="Browse messages safely. Messages are fetched with requeue (non-destructive)." />

          <Card pad={false} style={{ marginBottom: 18 }}>
            <div style={{ display: 'flex', divide: '1px' }}>
              <Stat label="Messages Ready" value="121" />
              <Stat label="Consumers" value="0" />
              <Stat label="Message Rate (in)" value="0.12" unit="/s" />
              <Stat label="Last Message" value="2m ago" />
              <Stat label="Preview Limit" value="100" info />
            </div>
          </Card>

          <Alert tone="info" style={{ marginBottom: 18 }}>Showing the latest 100 messages (preview limit). More messages may exist.</Alert>

          <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
            <div style={{ flex: 1 }}><SearchInput placeholder="Search in payload, headers…" /></div>
            <div style={{ width: 170 }}><Select options={['All Payload Types', 'JSON', 'TEXT', 'BASE64']} /></div>
            <div style={{ width: 120 }}><Select options={['All', 'x-death > 2']} /></div>
            <Button variant="ghost">Clear Filters</Button>
            <Button icon="refresh-cw">Refresh</Button>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 18, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--slate-500)' }}>Saved views:</span>
            {Object.keys(VIEWS).map((v) => (
              <button key={v} onClick={() => setView(view === v ? null : v)}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', borderRadius: 999, cursor: 'pointer', fontFamily: 'var(--font-ui)', fontSize: 12, fontWeight: 600, border: `1px solid ${view === v ? 'var(--blue-200)' : 'var(--border-default)'}`, background: view === v ? 'var(--blue-50)' : 'var(--surface-control)', color: view === v ? 'var(--blue-600)' : 'var(--slate-600)' }}>
                <Icon name="bookmark" size={11} />{v}
              </button>
            ))}
            {view && <IconButton icon="link" size={26} bordered={false} title="Copy shareable link" />}
            <IconButton icon="bookmark-plus" size={26} bordered={false} title="Save current filters as view" />
          </div>

          {deletedNote > 0 && (
            <Alert tone="info" icon="trash-2" style={{ marginBottom: 18 }}
              action={<a href="#" onClick={(e) => { e.preventDefault(); nav('parking'); }} style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-link)', textDecoration: 'none' }}>View Recently Deleted</a>}>
              {deletedNote} {deletedNote === 1 ? 'message' : 'messages'} moved to Recently Deleted — restorable for 24 hours.
            </Alert>
          )}

          <Card pad={false}>
            {checked.length > 0 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 20px', background: confirmDelete ? 'var(--red-50)' : 'var(--blue-50)', borderBottom: `1px solid ${confirmDelete ? 'var(--red-200)' : 'var(--blue-200)'}`, borderRadius: 'var(--radius-lg) var(--radius-lg) 0 0' }}>
                {confirmDelete ? (
                  <React.Fragment>
                    <Icon name="alert-triangle" size={16} color="var(--red-600)" />
                    <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--red-700)' }}>Delete {checked.length} {checked.length === 1 ? 'message' : 'messages'} from {queue}?</span>
                    <span style={{ fontSize: 12.5, color: 'var(--slate-600)' }}>This cannot be undone. Consider parking instead.</span>
                    <div style={{ flex: 1 }} />
                    <Button variant="secondary" size="sm" onClick={() => setConfirmDelete(false)}>Cancel</Button>
                    <Button variant="dangerSolid" size="sm" icon="trash-2" onClick={doDelete}>Delete {checked.length}</Button>
                  </React.Fragment>
                ) : (
                  <React.Fragment>
                    <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--blue-700)' }}>{checked.length} selected</span>
                    <a href="#" onClick={(e) => { e.preventDefault(); clearSel(); }} style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-link)', textDecoration: 'none' }}>Clear</a>
                    <div style={{ flex: 1 }} />
                    <Button size="sm" icon="play" disabled={!canAct} onClick={() => bulkNav('move')}>Replay (Move)</Button>
                    <Button size="sm" variant="secondary" icon="copy" disabled={!canAct} onClick={() => bulkNav('copy')} style={{ color: 'var(--text-link)' }}>Replay (Copy)</Button>
                    <Button size="sm" variant="park" icon="flag" disabled={!canAct} onClick={() => bulkNav('park')}>Park</Button>
                    <Button size="sm" variant="danger" icon="trash-2" disabled={!canDelete} onClick={() => setConfirmDelete(true)}>{canDelete ? 'Delete' : 'Delete (Admin only)'}</Button>
                  </React.Fragment>
                )}
              </div>
            )}
            <DataTable rowKey="id" sortKey="at" onRowClick={(r) => { setSelected(r.id); setPanelOpen(true); }} selectedKey={selected}
              columns={[
                { key: 'sel', label: <span onClick={(e) => e.stopPropagation()}><Checkbox checked={allChecked} onChange={toggleAll} /></span>, width: 34, render: (r) => (
                  <span onClick={(e) => e.stopPropagation()}><Checkbox checked={checked.includes(r.id)} onChange={() => toggle(r.id)} /></span>) },
                { key: 'id', label: 'Message ID', render: (r) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--slate-700)' }}>{r.id}</span> },
                { key: 'at', label: 'Published At' },
                { key: 'type', label: 'Payload Type', render: (r) => <Badge tone={PAYLOAD_TONE[r.type] || 'neutral'}>{r.type}</Badge> },
                { key: 'size', label: 'Size' },
                { key: 'xdeath', label: 'x-death (count)' },
                { key: 'preview', label: 'Preview', render: (r) => <span style={{ fontSize: 12.5, color: 'var(--slate-500)' }}>{r.preview}</span> },
                { key: 'a', label: 'Actions', align: 'right', render: () => (
                  <span style={{ display: 'inline-flex', gap: 6 }}>
                    <IconButton icon="eye" size={28} />
                    <IconButton icon="ellipsis" size={28} />
                  </span>) },
              ]}
              rows={visibleRows} />
            <div style={{ display: 'flex', alignItems: 'center', padding: '14px 20px', borderTop: '1px solid var(--slate-100)' }}>
              <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{view ? `View “${view}” · showing ${visibleRows.length} of ${rows.length} fetched` : `Showing 1 to ${rows.length} of 121 messages`}</span>
              <div style={{ flex: 1 }} />
              <Pagination page={1} pageCount={2} />
              <div style={{ width: 120, marginLeft: 10 }}><Select options={['100 / page', '50 / page', '25 / page']} /></div>
            </div>
          </Card>
        </div>

        {panelOpen && (
          <aside style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none', background: 'var(--surface-card)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-lg)', padding: 20, boxSizing: 'border-box', position: 'sticky', top: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-heading)', flex: 1 }}>Message Details</div>
              <IconButton icon="x" bordered={false} size={28} onClick={() => setPanelOpen(false)} />
            </div>
            <KeyValue gap={12} items={[
              { label: 'ID', value: msg.id, mono: true, copy: true },
              { label: 'Published At', value: msg.at },
              { label: 'Size', value: msg.size },
              { label: 'Payload Type', value: <Badge tone={PAYLOAD_TONE[msg.type] || 'neutral'}>{msg.type}</Badge> },
            ]} />
            <div style={{ marginTop: 16 }}>
              <Tabs active={tab} onChange={setTab} tabs={[
                { id: 'payload', label: 'Payload' }, { id: 'headers', label: 'Headers' },
                { id: 'xdeath', label: 'x-death' }, { id: 'journey', label: 'Journey' }]} style={{ gap: 16 }} />
              <div style={{ marginTop: 12 }}>
                {tab === 'payload' && <CodeBlock code={D.payload} maxHeight={300} />}
                {tab === 'headers' && <CodeBlock code={'x-death: [3 entries]\nx-first-death-exchange: email.exchange\nx-first-death-queue: email.retry\nx-first-death-reason: rejected'} maxHeight={300} />}
                {tab === 'xdeath' && <XDeathTable rows={D.xdeath} />}
                {tab === 'journey' && <Journey />}
              </div>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 16 }}>
              <Button size="sm" disabled={!canAct} onClick={() => nav('replay', { msg, mode: 'move' })}>Replay (Move)</Button>
              <Button size="sm" variant="secondary" disabled={!canAct} onClick={() => nav('replay', { msg, mode: 'copy' })} style={{ color: 'var(--text-link)' }}>Replay (Copy)</Button>
              <Button size="sm" variant="park" icon="flag" disabled={!canAct} onClick={() => nav('replay', { msg, mode: 'park' })}>Park</Button>
              <Button size="sm" variant="danger" icon="trash-2" disabled={!canDelete}>Delete</Button>
            </div>
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid var(--slate-100)' }}>
              <div style={{ display: 'flex', alignItems: 'center', fontSize: 14, fontWeight: 600, color: 'var(--text-heading)' }}>
                Message Fingerprint <Icon name="chevron-down" size={15} color="var(--slate-400)" style={{ marginLeft: 'auto' }} />
              </div>
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginTop: 8, padding: '5px 10px', background: 'var(--slate-50)', border: '1px solid var(--border-default)', borderRadius: 6, fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--slate-700)' }}>
                b7c9e5d2f4a1c8e7 <Icon name="copy" size={13} color="var(--slate-400)" />
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 12 }}>x-death Count</div>
              <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--slate-900)', marginTop: 2 }}>{msg.xdeath}</div>
            </div>
          </aside>
        )}
      </div>
    );
  }

  window.QL.screens.Messages = Messages;
})();
