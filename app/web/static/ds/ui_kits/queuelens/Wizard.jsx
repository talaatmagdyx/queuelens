// Replay / Park wizard (Select Action → Destination → Confirm → Review & Execute).
(function () {
  const { Icon, Badge, Button, Alert, Select, Input, Switch, Stepper, ActionChoiceCard, StatusPill, KeyValue } = window.__NS;
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

  function Wizard({ nav, msg = D.messages[0], mode = 'move', count = 1 }) {
    const many = count > 1;
    const noun = many ? `${count} messages` : 'this message';
    const [action, setAction] = React.useState(mode);
    const [confirm, setConfirm] = React.useState('');
    const [headers, setHeaders] = React.useState(true);
    const [stage, setStage] = React.useState('form'); // form | review | running | done
    const [prog, setProg] = React.useState(0);
    const [editPayload, setEditPayload] = React.useState(false);
    const [payload, setPayload] = React.useState(D.payload);
    const [when, setWhen] = React.useState('Execute immediately');
    const [throttle, setThrottle] = React.useState('10 msg/s');
    const payloadValid = (() => { try { JSON.parse(payload); return true; } catch (e) { return false; } })();
    const scheduled = when !== 'Execute immediately';
    React.useEffect(() => {
      if (stage !== 'running') return;
      const t = setInterval(() => {
        setProg((p) => {
          if (p >= 100) { clearInterval(t); setTimeout(() => setStage('done'), 400); return 100; }
          return Math.min(100, p + 4);
        });
      }, 60);
      return () => clearInterval(t);
    }, [stage]);
    const isPark = action === 'park';
    const srcQueue = 'payments.retry.dlq';
    const confirmed = confirm === srcQueue;
    const title = isPark ? 'Park Message' : 'Replay Message';
    const step = stage === 'review' || stage === 'running' ? 3 : confirmed ? 2 : isPark ? 1 : 0;
    const stepLabels = ['Select Action', isPark ? 'Parking Destination' : 'Target Destination', 'Confirm & Safety', 'Review & Execute'];
    const destination = isPark ? `parking.exchange → parking.${srcQueue}` : 'email.exchange → email.processed';
    const ACTION_LABEL = { move: 'Replay (Move)', copy: 'Replay (Copy)', park: 'Park' };

    if (stage === 'done') {
      return (
        <div style={{ maxWidth: 620, margin: '60px auto', textAlign: 'center' }}>
          <div style={{ width: 64, height: 64, borderRadius: 999, background: 'var(--green-100)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 18px' }}>
            <Icon name="check" size={30} color="var(--green-600)" strokeWidth={2.5} />
          </div>
          <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--text-heading)' }}>{scheduled ? `${many ? count + ' messages' : 'Message'} scheduled` : isPark ? (many ? `${count} messages parked` : 'Message parked') : (many ? `${count} messages replayed` : 'Message replayed')}</div>
          <div style={{ fontSize: 14, color: 'var(--text-body)', marginTop: 8, lineHeight: 1.6 }}>
            {scheduled ? `Replay scheduled for 02:00 at ${throttle} to email.exchange / email.processed.` : isPark ? `${many ? 'The messages were' : 'The message was'} published to parking.payments.retry.dlq.` : `${many ? 'The messages were' : 'The message was'} published to email.exchange / email.processed${action === 'move' ? ' and removed from ' + srcQueue : ''}.`}<br />
            {scheduled ? 'You can cancel from the audit log until it starts.' : `${many ? 'The actions were' : 'The action was'} recorded in the audit log.`}
          </div>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginTop: 26 }}>
            <Button variant="secondary" onClick={() => nav('audit')}>View audit log</Button>
            <Button onClick={() => nav('messages')}>Back to Messages</Button>
          </div>
        </div>
      );
    }

    if (stage === 'review' || stage === 'running') {
      const running = stage === 'running';
      const nth = Math.max(1, Math.min(count, Math.ceil((prog / 100) * count)));
      return (
        <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Breadcrumbs items={[
              { label: 'Queues', onClick: () => nav('queues') },
              { label: srcQueue, onClick: () => nav('messages') },
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
                    { label: 'Payload', value: editPayload ? (payloadValid ? 'Edited — valid JSON' : 'Edited — INVALID') : 'Original (unchanged)' },
                    { label: 'Replay Headers', value: isPark || headers ? 'Yes — x-queuelens-*' : 'No' },
                  ]} />
                </div>
              </Card>
            </div>
            <div style={{ marginTop: 14 }}>
              <Card title="Execution">
                <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr', gap: 14, marginTop: 4 }}>
                  <Select label="When" options={['Execute immediately', 'Schedule for later']} value={when} onChange={setWhen} />
                  {scheduled && <Input label="At" defaultValue="02:00" />}
                  {scheduled && <Select label="Throttle" options={['10 msg/s', '1 msg/s', '50 msg/s', 'No limit']} value={throttle} onChange={setThrottle} />}
                </div>
                {scheduled && <Alert tone="info" style={{ marginTop: 12 }}>Scheduled replays run under the same audit and publish-before-ack guarantees. You can cancel from the audit log until they start.</Alert>}
              </Card>
            </div>
            <Alert tone={action === 'copy' ? 'success' : 'warning'} style={{ marginTop: 14 }}
              title={action === 'move' ? `The original ${many ? 'messages' : 'message'} will be removed from ${srcQueue} after each successful publish.` : action === 'copy' ? 'Non-destructive: the source queue is not modified.' : `${many ? 'Messages' : 'The message'} will be held in parking.${srcQueue}.`}>
              Publish-before-ack: nothing is acknowledged until the publish is confirmed by the broker.
            </Alert>
            {running && (
              <div style={{ marginTop: 14, padding: '16px 20px', background: 'var(--surface-card)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-lg)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600, color: 'var(--text-heading)' }}>
                  <Icon name="loader" size={16} color="var(--blue-600)" />
                  {prog >= 100 ? 'Finalizing…' : many ? `Publishing message ${nth} of ${count}…` : 'Publishing message…'}
                </div>
                <div style={{ height: 8, borderRadius: 999, background: 'var(--slate-100)', marginTop: 12, overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: prog + '%', borderRadius: 999, background: 'var(--blue-600)', transition: 'width 60ms linear' }}></div>
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 8 }}>{destination} · publish-before-ack · audited</div>
              </div>
            )}
            <div style={{ display: 'flex', marginTop: 26, paddingBottom: 30 }}>
              <Button variant="secondary" disabled={running} onClick={() => nav('messages')}>Cancel</Button>
              <div style={{ flex: 1 }} />
              <div style={{ display: 'flex', gap: 10 }}>
                <Button variant="secondary" disabled={running} onClick={() => setStage('form')}>Back</Button>
                <Button icon={scheduled ? 'calendar-clock' : 'play'} disabled={running || (editPayload && !payloadValid)} onClick={() => { if (scheduled) { setStage('done'); } else { setProg(0); setStage('running'); } }}>
                  {running ? 'Executing…' : scheduled ? 'Schedule Replay' : `Execute ${ACTION_LABEL[action]}`}
                </Button>
              </div>
            </div>
          </div>
          <div style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none' }}>
            <MessageSummaryPanel msg={msg} payload={D.payload} xdeath={D.xdeath} />
          </div>
        </div>
      );
    }

    return (
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Breadcrumbs items={[
            { label: 'Queues', onClick: () => nav('queues') },
            { label: srcQueue, onClick: () => nav('messages') },
            { label: 'Messages', onClick: () => nav('messages') },
            { label: isPark ? 'Park' : 'Replay' }]} />
          <PageHeader title={title} subtitle={isPark ? `Send ${noun} to a parking queue for safe manual inspection.` : `Send ${noun} to another exchange and routing key.`} />

          {many && (
            <Alert tone="info" icon="layers" style={{ marginBottom: 16 }}>
              <b style={{ color: 'var(--blue-700)' }}>Bulk action:</b> {count} messages from {srcQueue}. They will be processed one by one, publish-before-ack. The summary panel shows the first message.
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

          <SectionTitle n="2" title={isPark ? 'Parking Destination' : 'Target Destination'} subtitle={isPark ? 'Choose where to park this message for later inspection.' : 'Choose where to send this message.'} />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.3fr 1.3fr 0.8fr', gap: 14 }}>
            <Select label="Exchange Type" options={['Direct', 'Topic', 'Fanout']} />
            <Select label="Exchange" required options={isPark ? ['parking.exchange'] : ['email.exchange', 'demo.exchange']} />
            <Select label="Routing Key" required options={isPark ? [srcQueue] : ['email.processed', 'demo.processed']} />
            <Select label="VHost" options={['/']} />
          </div>
          {!isPark && (
            <div style={{ marginTop: 16 }}>
              <Switch checked={headers} onChange={setHeaders} label="Add Replay Headers" description="Add headers to identify this message was replayed by QueueLens." />
              {headers && (
                <Alert tone="info" style={{ marginTop: 12 }}>
                  <b style={{ color: 'var(--blue-700)' }}>Headers added:</b>&nbsp; x-queuelens-replayed: true, x-queuelens-source-queue, x-queuelens-replayed-at, x-queuelens-action
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
                  <Input label="Queue Name" defaultValue={'parking.' + srcQueue} readOnly />
                  <Input label="Current Messages" defaultValue="12" readOnly />
                </div>
                <Alert tone="success" style={{ marginTop: 12 }}>Queue exists and is ready to receive messages.</Alert>
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
                    <HeaderRow k="x-queuelens-parked-at" v="2024-05-21T10:24:15.123Z" />
                    <HeaderRow k="x-queuelens-source-queue" v={srcQueue} />
                    <HeaderRow k="x-queuelens-message-id" v={msg.id} />
                  </tbody>
                </table>
              </Card>
            </div>
          )}

          {!isPark && (
            <div style={{ marginTop: 16 }}>
              <Switch checked={editPayload} onChange={setEditPayload} label="Edit payload before replay" description="Fix the field that caused the failure. The original payload is preserved in the audit trail." />
              {editPayload && (
                <div style={{ position: 'relative', marginTop: 12 }}>
                  <textarea value={payload} onChange={(e) => setPayload(e.target.value)} spellCheck={false}
                    style={{ width: '100%', boxSizing: 'border-box', minHeight: 180, resize: 'vertical', padding: '12px 14px', fontFamily: 'var(--font-mono)', fontSize: 12.5, lineHeight: 1.7, color: 'var(--slate-700)', background: 'var(--slate-50)', border: `1px solid ${payloadValid ? 'var(--border-default)' : 'var(--red-200)'}`, borderRadius: 'var(--radius-md)', outline: 'none' }} />
                  <span style={{ position: 'absolute', top: 10, right: 12 }}><Badge tone={payloadValid ? 'success' : 'danger'}>{payloadValid ? 'VALID JSON' : 'INVALID JSON'}</Badge></span>
                  <a href="#" onClick={(e) => { e.preventDefault(); setPayload(D.payload); }} style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-link)', textDecoration: 'none' }}>Reset to original</a>
                </div>
              )}
            </div>
          )}

          <SectionTitle n="3" title="Confirm & Safety" subtitle={`Type the queue name to confirm you want to ${isPark ? 'park' : 'replay'} ${noun}.`} />
          <Alert tone="warning" title={`Important: This action will ${isPark ? 'park' : 'replay'} ${noun}.`}>
            {action === 'move' ? `Move replay will remove ${many ? 'the messages' : 'the message'} from ${srcQueue} after each successful publish.` : action === 'copy' ? `Copy replay leaves the original ${many ? 'messages' : 'message'} in place.` : `${many ? 'The messages' : 'The message'} will be held in parking.${srcQueue} until you act on ${many ? 'them' : 'it'}.`}
          </Alert>
          <div style={{ marginTop: 14 }}>
            <Input label="Type the source queue name to confirm" required placeholder={srcQueue} value={confirm} onChange={setConfirm} valid={confirmed} />
          </div>

          <div style={{ display: 'flex', marginTop: 26, paddingBottom: 30 }}>
            <Button variant="secondary" onClick={() => nav('messages')}>Cancel</Button>
            <div style={{ flex: 1 }} />
            <div style={{ display: 'flex', gap: 10 }}>
              <Button variant="secondary" onClick={() => nav('messages')}>Back</Button>
              <Button iconRight="arrow-right" disabled={!confirmed} onClick={() => setStage('review')}>
                Review & Execute
              </Button>
            </div>
          </div>
        </div>

        <div style={{ width: 'clamp(280px, 26vw, 360px)', flex: 'none' }}>
          <MessageSummaryPanel msg={msg} payload={D.payload} xdeath={D.xdeath} />
        </div>
      </div>
    );
  }

  window.QL.screens.Wizard = Wizard;
})();
