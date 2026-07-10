// Parking screen — live parking-lot contents plus the delete history from the audit log.
(function () {
  const { Icon, Badge, StatusPill, Button, DataTable, Tabs, Alert } = window.__NS;
  const { PageHeader, Card, EmptyState, PAYLOAD_TONE } = window.QL;

  function Parking({ nav }) {
    const [tab, setTab] = React.useState('lot');
    const parked = React.useMemo(() => window.QL.fetchParked(), []);
    const deletes = React.useMemo(
      () => window.QL.fetchAudit().filter((r) => r.action === 'delete' && r.result !== 'Started'),
      []);
    const parkingQueues = new Set(parked.map((r) => r.parkingQueue)).size;

    return (
      <div>
        <PageHeader title="Parking" subtitle="Messages held in parking queues for manual inspection, and the permanent delete history." />
        <Card pad={false}>
          <div style={{ padding: '14px 20px 0' }}>
            <Tabs active={tab} onChange={setTab} tabs={[
              { id: 'lot', label: 'Parking Lot', count: parked.length },
              { id: 'deletes', label: 'Delete History', count: deletes.length }]} />
          </div>
          {tab === 'lot' ? (
            parked.length === 0 ? (
              <EmptyState icon="flag" tone="info" title="Parking lot is empty"
                actions={<Button variant="secondary" onClick={() => nav('queues')}>Browse queues</Button>}>
                Park a message from any queue to hold it here for inspection.
              </EmptyState>
            ) : (
              <DataTable rowKey="fingerprint"
                columns={[
                  { key: 'id', label: 'Message ID', render: (r) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{r.id}</span> },
                  { key: 'source', label: 'Source Queue', render: (r) => <a href="#" onClick={(e) => { e.preventDefault(); nav('queuedetail', { queue: r.source }); }} style={{ color: 'var(--text-link)', fontWeight: 600, textDecoration: 'none' }}>{r.source}</a> },
                  { key: 'parkingQueue', label: 'Parking Queue', render: (r) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--purple-600)' }}>{r.parkingQueue}</span> },
                  { key: 'type', label: 'Type', render: (r) => <Badge tone={PAYLOAD_TONE[r.type] || 'neutral'}>{r.type}</Badge> },
                  { key: 'size', label: 'Size' },
                  { key: 'by', label: 'Parked By' },
                  { key: 'age', label: 'Parked', render: (r) => <span style={{ fontWeight: 600, color: 'var(--slate-700)' }}>{r.age}</span> },
                  { key: 'a', label: 'Actions', align: 'right', render: (r) => (
                    <span style={{ display: 'inline-flex', gap: 6 }}>
                      <Button size="sm" onClick={() => nav('replay', { mode: 'move', msg: r.msg })}>Replay</Button>
                      <Button size="sm" variant="secondary" onClick={() => nav('messages', { queue: r.parkingQueue, fingerprint: r.fingerprint })}>Inspect</Button>
                    </span>) },
                ]}
                rows={parked} footer={`${parked.length} parked message${parked.length === 1 ? '' : 's'} across ${parkingQueues} parking queue${parkingQueues === 1 ? '' : 's'}`} />
            )
          ) : (
            deletes.length === 0 ? (
              <EmptyState icon="trash-2" tone="neutral" title="No deletes recorded">
                Every delete is written to the audit log with who, when, and which message.
              </EmptyState>
            ) : (
              <DataTable rowKey="key"
                columns={[
                  { key: 'time', label: 'Deleted At' },
                  { key: 'user', label: 'Deleted By' },
                  { key: 'queue', label: 'Source Queue' },
                  { key: 'fingerprint', label: 'Fingerprint', render: (r) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{r.fingerprint ? r.fingerprint.slice(0, 16) + '…' : '—'}</span> },
                  { key: 'result', label: 'Result', render: (r) => <StatusPill tone={r.result === 'Success' ? 'danger' : 'warning'}>{r.result}</StatusPill> },
                ]}
                rows={deletes} footer="Deletes are permanent — prefer Park when you might need the message again." />
            )
          )}
        </Card>
        <Alert tone="success" style={{ marginTop: 18 }} title="Messages never lost by parking">
          Parked messages live in real, durable parking queues on the broker — replay or delete them
          whenever you are ready. Deletes, by contrast, are permanent and always audited.
        </Alert>
      </div>
    );
  }

  window.QL.screens.Parking = Parking;
})();
