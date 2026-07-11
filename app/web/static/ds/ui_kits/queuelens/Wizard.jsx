// Replay / Park wizard (Select Action → Destination → Confirm → Review & Execute).
// Wired to the live QueueLens API: real targets, real execution, real results.
(function () {
  const { Icon, Badge, Button, Alert, Select, Input, Switch, Stepper, ActionChoiceCard, KeyValue } = window.__NS;
  const { PageHeader, Card, Breadcrumbs, MessageSummaryPanel } = window.QL;
  const D = window.QL.data;

  function SectionTitle({ n, title, subtitle }) {
    return (
      <div style={{ margin: '26px 0 14px' }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-heading)' }}>
          <span style={{ color: 'var(--blue-600)', marginRight: 6 }}>{n}.</span>{title}
        </div>
        {subtitle && <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 3 }}>{subtitle}</div>}
      </div>
    );
  }

  function HeaderRow({ k, v }) {
    return (
      <tr>
        <td style={{ padding: '10px 14px', fontSize: 13, color: 'var(--slate-600)', fontFamily: 'var(--font-mono)', borderTop: '1px solid var(--slate-100)' }}>{k}</td>
        <td style={{ padding: '10px 14px', fontSize: 13, color: 'var(--slate-700)', fontFamily: 'var(--font-mono)', borderTop: '1px solid var(--slate-100)' }}>{v}</td>
        <td style={{ padding: '10px 14px', textAlign: 'right', borderTop: '1px solid var(--slate-100)' }}><Icon name="copy" size={14} color="var(--slate-400)" /></td>
      </tr>
    );
  }

  const postJson = window.QL.postJson;

  function Wizard({ nav, msg = D.messages[0], mode = 'move', count = 1, fingerprints = null }) {
    const many = count > 1;
    const noun = many ? `${count} messages` : 'this message';
    const srcQueue = (msg && msg.queue) || window.QL.defaultQueue;
    const [action, setAction] = React.useState(mode);
    const ui = (window.QL.serverSettings || {}).ui || {};
    const isProduction = ((window.QL.broker || {}).environment || '') === 'production';
    // Type-to-confirm: always enforced in production; elsewhere the toggle may
    // pre-fill the field (the review step still stands between you and execute).
    const prefill = !isProduction && ui.typeConfirm === false;
    const [confirm, setConfirm] = React.useState(prefill ? srcQueue : '');
    const [headers, setHeaders] = React.useState(true);
    const [stage, setStage] = React.useState('form'); // form | review | running | done | failed
    const [error, setError] = React.useState(null);
    const [summary, setSummary] = React.useState(null);
    const exchanges = React.useMemo(() => window.QL.fetchExchanges(), []);
    const [exchange, setExchange] = React.useState('');
    const [routingKey, setRoutingKey] = React.useState('');
    const parkingInfo = React.useMemo(
      () => window.QL.fetchQueueInfo(srcQueue + '.parking'), [srcQueue]);

    const isPark = action === 'park';
    const confirmed = confirm === srcQueue;
    const title = isPark ? 'Park Message' : 'Replay Message';
    const step = stage === 'review' || stage === 'running' ? 3 : confirmed ? 2 : isPark ? 1 : 0;
    const stepLabels = ['Select Action', isPark ? 'Parking Destination' : 'Target Destination', 'Confirm & Safety', 'Review & Execute'];
    const exchangeType = (exchanges.find((e) => e.name === exchange) || { type: 'direct' }).type;
    const destination = isPark ? `${srcQueue}.parking`
      : `${exchange || '(default exchange)'} / ${routingKey || '—'}`;
    const targetValid = isPark || routingKey.trim().length > 0;
    const ACTION_LABEL = { move: 'Replay (Move)', copy: 'Replay (Copy)', park: 'Park' };

    const execute = async () => {
      setStage('running');
      setError(null);
      try {
        const target = isPark ? null
          : (exchange
            ? { type: 'exchange', exchange: exchange, routing_key: routingKey.trim() }
            : { type: 'queue', queue: routingKey.trim() });
        if (many && fingerprints && fingerprints.length > 1) {
          const preview = await postJson('/api/messages/bulk/dry-run', {
            source_queue: srcQueue,
            action: isPark ? 'park' : 'replay',
            mode: isPark ? undefined : action,
            target: target || undefined,
            fingerprints: fingerprints,
          });
          const outcome = await postJson('/api/messages/bulk/execute',
            { batch_id: preview.batch_id, confirm: true });
          setSummary(outcome.summary);
        } else if (isPark) {
          await postJson('/api/messages/park',
            { source_queue: srcQueue, fingerprint: msg.fingerprint, confirm: true });
        } else {
          await postJson('/api/messages/replay', {
            source_queue: srcQueue, fingerprint: msg.fingerprint, mode: action,
            confirm: true, annotate: headers, target: target,
          });
        }
        setStage('done');
      } catch (err) {
        setError(err.message);
        setStage('failed');
      }
    };

    if (stage === 'done') {
      return (
        <div style={{ maxWidth: 620, margin: '60px auto', textAlign: 'center' }}>
          <div style={{ width: 64, height: 64, borderRadius: 999, background: 'var(--green-100)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 18px' }}>
            <Icon name="check" size={30} color="var(--green-600)" strokeWidth={2.5} />
          </div>
          <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--text-heading)' }}>
            {isPark ? (many ? `${count} messages parked` : 'Message parked') : (many ? `${count} messages replayed` : 'Message replayed')}
          </div>
          <div style={{ fontSize: 14, color: 'var(--text-body)', marginTop: 8, lineHeight: 1.6 }}>
            {isPark
              ? `${many ? 'The messages were' : 'The message was'} published to ${srcQueue}.parking.`
              : `${many ? 'The messages were' : 'The message was'} published to ${destination}${action === 'move' ? ' and removed from ' + srcQueue : ''}.`}<br />
            {summary && `Result: ${summary.succeeded} succeeded, ${summary.failed} failed, ${summary.skipped_duplicates} duplicates skipped. `}
            {`${many ? 'The actions were' : 'The action was'} recorded in the audit log.`}
          </div>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginTop: 26 }}>
            <Button variant="secondary" onClick={() => nav('audit')}>View audit log</Button>
            <Button onClick={() => nav('messages', { queue: srcQueue })}>Back to Messages</Button>
          </div>
        </div>
      );
    }

    if (stage === 'failed') {
      return (
        <div style={{ maxWidth: 620, margin: '60px auto', textAlign: 'center' }}>
          <div style={{ width: 64, height: 64, borderRadius: 999, background: 'var(--red-100)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 18px' }}>
            <Icon name="x" size={30} color="var(--red-600)" strokeWidth={2.5} />
          </div>
          <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--text-heading)' }}>{ACTION_LABEL[action]} failed</div>
          <div style={{ fontSize: 14, color: 'var(--text-body)', marginTop: 8, lineHeight: 1.6 }}>
            {error}<br />Nothing was removed from {srcQueue} — failed publishes never ack the original.
          </div>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginTop: 26 }}>
            <Button variant="secondary" onClick={() => setStage('form')}>Back to form</Button>
            <Button onClick={() => nav('messages', { queue: srcQueue })}>Back to Messages</Button>
          </div>
        </div>
      );
    }

    if (stage === 'review' || stage === 'running') {
      const running = stage === 'running';
      return (
        <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Breadcrumbs items={[
              { label: 'Queues', onClick: () => nav('queues') },
              { label: srcQueue, onClick: () => nav('messages', { queue: srcQueue }) },
              { label: isPark ? 'Park' : 'Replay' },
              { label: 'Review & Execute' }]} />
            <PageHeader title="Review & Execute" subtitle={`Final check before ${isPark ? 'parking' : 'replaying'} ${noun}.`} />
            <Stepper steps={stepLabels} current={3} />
            <div style={{ marginTop: 22 }}>
              <Card title="Summary">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 4 }}>
                  <KeyValue gap={14} items={[
                    { label: 'Action', value: ACTION_LABEL[action] },
                    { label: 'Source Queue', value: <Badge tone="danger" uppercase={false}>{srcQueue}</Badge> },
                    { label: 'Destination', value: destination, mono: true },
                  ]} />
                  <KeyValue gap={14} items={[
                    { label: 'Messages', value: String(count) },
                    { label: 'Payload', value: 'Original (unchanged)' },
                    { label: 'Replay Headers', value: isPark || headers ? 'Yes — x-queuelens-*' : 'No' },
                  ]} />
                </div>
              </Card>
            </div>
            <Alert tone={action === 'copy' ? 'success' : 'warning'} style={{ marginTop: 14 }}
              title={action === 'move' ? `The original ${many ? 'messages' : 'message'} will be removed from ${srcQueue} after each successful publish.` : action === 'copy' ? 'Non-destructive: the source queue is not modified.' : `${many ? 'Messages' : 'The message'} will be held in ${srcQueue}.parking.`}>
              Publish-before-ack: nothing is acknowledged until the publish is confirmed by the broker.
            </Alert>
            {running && (
              <div style={{ marginTop: 14, padding: '16px 20px', background: 'var(--surface-card)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-lg)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600, color: 'var(--text-heading)' }}>
                  <Icon name="loader" size={16} color="var(--blue-600)" />
                  {many ? `Executing on ${count} messages…` : 'Publishing message…'}
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 8 }}>{destination} · publish-before-ack · audited</div>
              </div>
            )}
            <div style={{ display: 'flex', marginTop: 26, paddingBottom: 30 }}>
              <Button variant="secondary" disabled={running} onClick={() => nav('messages', { queue: srcQueue })}>Cancel</Button>
              <div style={{ flex: 1 }} />
              <div style={{ display: 'flex', gap: 10 }}>
                <Button variant="secondary" disabled={running} onClick={() => setStage('form')}>Back</Button>
                <Button icon="play" disabled={running} onClick={execute}>
                  {running ? 'Executing…' : `Execute ${ACTION_LABEL[action]}`}
                </Button>
              </div>
            </div>
          </div>
          <div style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none' }}>
            <MessageSummaryPanel msg={msg} payload={msg.payloadText || D.payload} xdeath={msg.xdeathList || D.xdeath} />
          </div>
        </div>
      );
    }

    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Breadcrumbs items={[
            { label: 'Queues', onClick: () => nav('queues') },
            { label: srcQueue, onClick: () => nav('messages', { queue: srcQueue }) },
            { label: 'Messages', onClick: () => nav('messages', { queue: srcQueue }) },
            { label: isPark ? 'Park' : 'Replay' }]} />
          <PageHeader title={title} subtitle={isPark ? `Send ${noun} to a parking queue for safe manual inspection.` : `Send ${noun} to another exchange and routing key.`} />

          {many && (
            <Alert tone="info" icon="layers" style={{ marginBottom: 16 }}>
              <b style={{ color: 'var(--blue-700)' }}>Bulk action:</b> {count} messages from {srcQueue}. They will be processed publish-before-ack with per-message results. The summary panel shows the first message.
            </Alert>
          )}

          <Stepper steps={stepLabels} current={step} />

          <SectionTitle n="1" title="Select Replay Action" subtitle="Choose how you want to send this message." />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
            <ActionChoiceCard icon="play" tone="info" title="Replay (Move)" description="Publish to target and remove from the source queue." tag="Recommended" tagTone="info" selected={action === 'move'} onSelect={() => setAction('move')} />
            <ActionChoiceCard icon="copy" tone="success" title="Replay (Copy)" description="Publish a copy to target without removing from source." tag="Non-destructive" tagTone="success" selected={action === 'copy'} onSelect={() => setAction('copy')} />
            <ActionChoiceCard icon="flag" tone="park" title="Park Message" description="Send to a parking queue for manual inspection." tag="Safe Holding" tagTone="park" selected={action === 'park'} onSelect={() => setAction('park')} />
          </div>
          <Alert tone="success" style={{ marginTop: 14 }}>
            <b style={{ color: 'var(--green-700)' }}>Safety:</b> {action === 'move' ? 'Move replay publishes the message first. The original message is removed only after publish succeeds.' : action === 'copy' ? 'Copy replay never touches the original message.' : 'The message will be published to the parking destination first (safe mode).'}
          </Alert>

          <SectionTitle n="2" title={isPark ? 'Parking Destination' : 'Target Destination'} subtitle={isPark ? 'The parking queue is derived from the source queue and created on demand.' : 'Choose where to send this message. Leave Exchange as default to publish straight to a queue.'} />
          {!isPark && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.3fr 1.3fr 0.8fr', gap: 14 }}>
              <Input label="Exchange Type" value={exchange ? exchangeType : 'direct'} readOnly />
              <Select label="Exchange" options={[{ value: '', label: '(default exchange)' }].concat(exchanges.filter((e) => e.name).map((e) => ({ value: e.name, label: e.name })))} value={exchange} onChange={setExchange} />
              <Input label={exchange ? 'Routing Key' : 'Target Queue'} required placeholder={exchange ? 'routing key' : 'queue name'} value={routingKey} onChange={setRoutingKey} />
              <Input label="VHost" value={(window.QL.broker || {}).vhost || '/'} readOnly />
            </div>
          )}
          {!isPark && (
            <div style={{ marginTop: 16 }}>
              <Switch checked={headers} onChange={setHeaders} label="Add Replay Headers" description="Add headers to identify this message was replayed by QueueLens." />
              {headers && (
                <Alert tone="info" style={{ marginTop: 12 }}>
                  <b style={{ color: 'var(--blue-700)' }}>Headers added:</b>&nbsp; x-queuelens-replayed: true, x-queuelens-action, x-queuelens-source-queue, x-queuelens-replayed-at, x-queuelens-replayed-by, x-queuelens-original-fingerprint
                </Alert>
              )}
            </div>
          )}
          {isPark && (
            <div style={{ marginTop: 16 }}>
              <Alert tone="info">
                The message will be published to the parking destination first (safe mode). You can inspect it later and decide to replay or delete.
              </Alert>
              <Card title="Parking Queue (Result)" subtitle="This is the queue where the message will be parked." style={{ marginTop: 14 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', gap: 14 }}>
                  <Input label="Queue Name" value={srcQueue + '.parking'} readOnly />
                  <Input label="Current Messages" value={parkingInfo ? String(parkingInfo.messages) : '—'} readOnly />
                </div>
                <Alert tone={parkingInfo ? 'success' : 'info'} style={{ marginTop: 12 }}>
                  {parkingInfo ? 'Queue exists and is ready to receive messages.' : 'Queue does not exist yet — it will be created (durable) on first park.'}
                </Alert>
              </Card>
              <Card title="Parking Headers" subtitle="Additional headers will be added to the parked message." style={{ marginTop: 14 }} pad={false}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: 'left', padding: '8px 14px', fontSize: 12.5, fontWeight: 600, color: 'var(--slate-500)', background: 'var(--surface-table-header)' }}>Header</th>
                      <th style={{ textAlign: 'left', padding: '8px 14px', fontSize: 12.5, fontWeight: 600, color: 'var(--slate-500)', background: 'var(--surface-table-header)' }}>Value</th>
                      <th style={{ background: 'var(--surface-table-header)' }}></th>
                    </tr>
                  </thead>
                  <tbody>
                    <HeaderRow k="x-queuelens-action" v="park" />
                    <HeaderRow k="x-queuelens-parked-at" v="set at execution time" />
                    <HeaderRow k="x-queuelens-source-queue" v={srcQueue} />
                    <HeaderRow k="x-queuelens-original-fingerprint" v={(msg.fingerprint || '').slice(0, 24) + '…'} />
                  </tbody>
                </table>
              </Card>
            </div>
          )}

          <SectionTitle n="3" title="Confirm & Safety" subtitle={`Type the queue name to confirm you want to ${isPark ? 'park' : 'replay'} ${noun}.`} />
          <Alert tone="warning" title={`Important: This action will ${isPark ? 'park' : 'replay'} ${noun}.`}>
            {action === 'move' ? `Move replay will remove ${many ? 'the messages' : 'the message'} from ${srcQueue} after each successful publish.` : action === 'copy' ? `Copy replay leaves the original ${many ? 'messages' : 'message'} in place.` : `${many ? 'The messages' : 'The message'} will be held in ${srcQueue}.parking until you act on ${many ? 'them' : 'it'}.`}
          </Alert>
          <div style={{ marginTop: 14 }}>
            <Input label="Type the source queue name to confirm" required placeholder={srcQueue} value={confirm} onChange={setConfirm} valid={confirmed} />
          </div>

          <div style={{ display: 'flex', marginTop: 26, paddingBottom: 30 }}>
            <Button variant="secondary" onClick={() => nav('messages', { queue: srcQueue })}>Cancel</Button>
            <div style={{ flex: 1 }} />
            <div style={{ display: 'flex', gap: 10 }}>
              <Button variant="secondary" onClick={() => nav('messages', { queue: srcQueue })}>Back</Button>
              <Button iconRight="arrow-right" disabled={!confirmed || !targetValid} onClick={() => setStage('review')}>
                Review & Execute
              </Button>
            </div>
          </div>
        </div>

        <div style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none' }}>
          <MessageSummaryPanel msg={msg} payload={msg.payloadText || D.payload} xdeath={msg.xdeathList || D.xdeath} />
        </div>
      </div>
    );
  }

  window.QL.screens.Wizard = Wizard;
})();
