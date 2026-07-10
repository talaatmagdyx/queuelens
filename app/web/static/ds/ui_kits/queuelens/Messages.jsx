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

  const REASON_META = {
    rejected: { icon: 'x-circle', color: 'var(--red-600)', label: 'Rejected by consumer' },
    expired: { icon: 'clock', color: 'var(--amber-600)', label: 'TTL expired' },
    maxlen: { icon: 'alert-triangle', color: 'var(--amber-600)', label: 'Queue length limit hit' },
    delivery_limit: { icon: 'alert-triangle', color: 'var(--red-600)', label: 'Delivery limit reached' },
  };

  function Journey({ msg }) {
    const events = (msg.xdeathList || []).map((d) => {
      const meta = REASON_META[d.reason] || { icon: 'corner-down-right', color: 'var(--slate-500)', label: d.reason };
      return { t: d.time, label: meta.label + ' in ' + d.queue, icon: meta.icon, color: meta.color, sub: 'x-death: ' + d.reason + ' · count ' + d.count };
    });
    if (!events.length) {
      return <div style={{ fontSize: 13, color: 'var(--text-muted)', padding: '8px 2px' }}>
        No dead-letter history recorded for this message — it was published to this queue directly.
      </div>;
    }
    const JOURNEY = events;
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
    const initialRows = React.useMemo(
      () => (queue === window.QL.defaultQueue ? D.messages : window.QL.fetchMessages(queue)),
      [queue]);
    const [rows, setRows] = React.useState(initialRows);
    React.useEffect(() => { setRows(initialRows); setChecked([]); }, [initialRows]);
    const [selected, setSelected] = React.useState(initialRows[0] ? initialRows[0].id : null);
    const [panelOpen, setPanelOpen] = React.useState(true);
    const [tab, setTab] = React.useState('payload');
    const [checked, setChecked] = React.useState([]);
    const [confirmDelete, setConfirmDelete] = React.useState(false);
    const [view, setView] = React.useState(null);
    const [deletedNote, setDeletedNote] = React.useState(0);
    const [search, setSearch] = React.useState('');
    const [typeFilter, setTypeFilter] = React.useState('All Payload Types');
    const [deleting, setDeleting] = React.useState(false);
    const queueRow = D.queues.find((q) => q.name === queue) || { messages: rows.length, ready: rows.length, consumers: 0, rate: null, last: '—', type: 'DLQ' };
    const QUEUE_TONE = { DLQ: 'danger', PARKING: 'success', NORMAL: 'info' };
    const msg = rows.find((m) => m.id === selected) || rows[0] || D.messages[0] || {};
    const allChecked = rows.length > 0 && checked.length === rows.length;
    const toggle = (id) => { setConfirmDelete(false); setChecked((c) => c.includes(id) ? c.filter((x) => x !== id) : [...c, id]); };
    const toggleAll = () => { setConfirmDelete(false); setChecked(allChecked ? [] : rows.map((r) => r.id)); };
    const clearSel = () => { setChecked([]); setConfirmDelete(false); };
    const bulkNav = (mode) => nav('replay', {
      msg: rows.find((r) => r.id === checked[0]) || msg, mode, count: checked.length,
      fingerprints: rows.filter((r) => checked.includes(r.id)).map((r) => r.fingerprint),
    });
    const api = window.QL.postJson;
    const doDelete = async () => {
      // Real deletion through the bulk API: dry-run on exactly the selected
      // fingerprints, then execute the returned one-shot batch.
      const fingerprints = rows.filter((r) => checked.includes(r.id)).map((r) => r.fingerprint);
      setDeleting(true);
      try {
        const preview = await api('/api/messages/bulk/dry-run',
          { source_queue: queue, action: 'delete', fingerprints });
        await api('/api/messages/bulk/execute', { batch_id: preview.batch_id, confirm: true });
        setRows((rs) => rs.filter((r) => !checked.includes(r.id)));
        setDeletedNote(fingerprints.length);
        clearSel();
      } catch (error) {
        window.alert('Delete failed: ' + error.message);
      } finally { setDeleting(false); }
    };
    const deleteOne = async () => {
      if (!window.confirm('Delete this message from ' + queue + '? This cannot be undone.')) return;
      try {
        await api('/api/messages/delete', { source_queue: queue, fingerprint: msg.fingerprint, confirm: true });
        setRows((rs) => rs.filter((r) => r.id !== msg.id));
        setDeletedNote(1);
      } catch (error) { window.alert('Delete failed: ' + error.message); }
    };
    const VIEWS = { 'x-death ≥ 3': (r) => r.xdeath >= 3, 'BASE64 payloads': (r) => r.type === 'BASE64', 'JSON only': (r) => r.type === 'JSON' };
    const visibleRows = rows.filter((r) => {
      if (view && !VIEWS[view](r)) return false;
      if (typeFilter !== 'All Payload Types' && r.type !== typeFilter) return false;
      if (search) {
        const haystack = (r.id + ' ' + r.payloadText + ' ' + r.headersText).toLowerCase();
        if (!haystack.includes(search.toLowerCase())) return false;
      }
      return true;
    });
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
              <StatusPill tone={QUEUE_TONE[queueRow.type] || 'info'} dot>{queueRow.type}{queueRow.retry ? ' · retry' : ''}</StatusPill>
            </span>}
            subtitle="Browse messages safely. Messages are fetched with requeue (non-destructive)." />

          <Card pad={false} style={{ marginBottom: 18 }}>
            <div style={{ display: 'flex', divide: '1px' }}>
              <Stat label="Messages Ready" value={String(queueRow.ready)} />
              <Stat label="Consumers" value={String(queueRow.consumers)} />
              <Stat label="Message Rate (in)" value={queueRow.rate != null ? String(queueRow.rate) : '—'} unit={queueRow.rate != null ? '/s' : undefined} />
              <Stat label="Last Message" value={queueRow.last} />
              <Stat label="Preview Limit" value={String(rows.length)} info />
            </div>
          </Card>

          {queueRow.messages > rows.length && (
            <Alert tone="info" style={{ marginBottom: 18 }}>Showing the latest {rows.length} messages (preview limit) of {queueRow.messages} in the queue.</Alert>
          )}

          <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
            <div style={{ flex: 1 }}><SearchInput placeholder="Search in payload, headers…" value={search} onChange={setSearch} /></div>
            <div style={{ width: 170 }}><Select options={['All Payload Types', 'JSON', 'TEXT', 'BASE64']} value={typeFilter} onChange={setTypeFilter} /></div>
            <Button variant="ghost" onClick={() => { setSearch(''); setTypeFilter('All Payload Types'); setView(null); }}>Clear Filters</Button>
            <Button icon="refresh-cw" onClick={() => location.reload()}>Refresh</Button>
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
              action={<a href="#" onClick={(e) => { e.preventDefault(); nav('audit'); }} style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-link)', textDecoration: 'none' }}>View Audit Log</a>}>
              {deletedNote} {deletedNote === 1 ? 'message' : 'messages'} deleted permanently. Every deletion is recorded in the audit log.
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
                    <Button variant="dangerSolid" size="sm" icon="trash-2" onClick={doDelete} disabled={deleting}>{deleting ? 'Deleting…' : 'Delete ' + checked.length}</Button>
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
              <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{view || search || typeFilter !== 'All Payload Types' ? `Filtered · showing ${visibleRows.length} of ${rows.length} fetched` : `Showing 1 to ${rows.length} of ${queueRow.messages} messages`}</span>
              <div style={{ flex: 1 }} />
              <Pagination page={1} pageCount={1} />
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
                {tab === 'payload' && <CodeBlock code={msg.payloadText || '{}'} maxHeight={300} />}
                {tab === 'headers' && <CodeBlock code={msg.headersText || '(no headers)'} maxHeight={300} />}
                {tab === 'xdeath' && ((msg.xdeathList || []).length
                  ? <XDeathTable rows={msg.xdeathList} />
                  : <div style={{ fontSize: 13, color: 'var(--text-muted)', padding: '8px 2px' }}>No x-death entries — this message has never been dead-lettered.</div>)}
                {tab === 'journey' && <Journey msg={msg} />}
              </div>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 16 }}>
              <Button size="sm" disabled={!canAct} onClick={() => nav('replay', { msg, mode: 'move' })}>Replay (Move)</Button>
              <Button size="sm" variant="secondary" disabled={!canAct} onClick={() => nav('replay', { msg, mode: 'copy' })} style={{ color: 'var(--text-link)' }}>Replay (Copy)</Button>
              <Button size="sm" variant="park" icon="flag" disabled={!canAct} onClick={() => nav('replay', { msg, mode: 'park' })}>Park</Button>
              <Button size="sm" variant="danger" icon="trash-2" disabled={!canDelete} onClick={deleteOne}>Delete</Button>
            </div>
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid var(--slate-100)' }}>
              <div style={{ display: 'flex', alignItems: 'center', fontSize: 14, fontWeight: 600, color: 'var(--text-heading)' }}>
                Message Fingerprint <Icon name="chevron-down" size={15} color="var(--slate-400)" style={{ marginLeft: 'auto' }} />
              </div>
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginTop: 8, padding: '5px 10px', background: 'var(--slate-50)', border: '1px solid var(--border-default)', borderRadius: 6, fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--slate-700)', cursor: 'pointer' }}
                title="Copy full fingerprint"
                onClick={() => navigator.clipboard && navigator.clipboard.writeText(msg.fingerprint || '')}>
                {(msg.fingerprint || '').slice(0, 16)} <Icon name="copy" size={13} color="var(--slate-400)" />
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
