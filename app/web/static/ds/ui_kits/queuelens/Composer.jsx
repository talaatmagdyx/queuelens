// Test message composer — publish a hand-written message through the real API.
(function () {
  const { Badge, Button, Alert, Select, Input, Switch } = window.__NS;
  const { PageHeader, Card } = window.QL;

  const SAMPLE = '{\n  "orderId": 12345,\n  "userId": 6789,\n  "amount": 259.99,\n  "currency": "USD",\n  "status": "pending"\n}';

  function Composer({ nav }) {
    const exchanges = React.useMemo(() => window.QL.fetchExchanges(), []);
    const [exchange, setExchange] = React.useState('');
    const [routingKey, setRoutingKey] = React.useState('');
    const [payload, setPayload] = React.useState(SAMPLE);
    const [markTest, setMarkTest] = React.useState(true);
    const [busy, setBusy] = React.useState(false);
    const [result, setResult] = React.useState(null); // { ok, text }
    const validJson = (() => { try { JSON.parse(payload); return true; } catch (e) { return false; } })();
    const exchangeType = (exchanges.find((e) => e.name === exchange) || { type: 'direct' }).type;
    const canSend = routingKey.trim().length > 0 && payload.length > 0 && !busy;
    const destination = (exchange || '(default exchange)') + ' / ' + (routingKey || '—');

    const send = async () => {
      setBusy(true);
      setResult(null);
      try {
        const outcome = await window.QL.postJson('/api/messages/publish', {
          exchange: exchange, routing_key: routingKey.trim(),
          payload: payload, mark_test: markTest, confirm: true,
        });
        setResult({ ok: true, text: `Published to ${destination} as ${outcome.content_type} in ${(outcome.duration_ms / 1000).toFixed(2)}s. The publish was broker-confirmed and audited.` });
      } catch (error) {
        setResult({ ok: false, text: error.message });
      } finally { setBusy(false); }
    };

    return (
      <div style={{ maxWidth: 860 }}>
        <PageHeader title="Test Message Composer" subtitle="Publish a hand-written message to verify a fix before replaying the real backlog." />
        <Card title="Destination" subtitle="Leave Exchange as default to publish straight to a queue.">
          <div style={{ display: 'grid', gridTemplateColumns: '0.8fr 1.3fr 1.3fr 0.6fr', gap: 14, marginTop: 8 }}>
            <Input label="Exchange Type" value={exchange ? exchangeType : 'direct'} readOnly />
            <Select label="Exchange" options={[{ value: '', label: '(default exchange)' }].concat(exchanges.filter((e) => e.name).map((e) => ({ value: e.name, label: e.name })))} value={exchange} onChange={setExchange} />
            <Input label={exchange ? 'Routing Key' : 'Target Queue'} required placeholder={exchange ? 'routing key' : 'queue name'} value={routingKey} onChange={(v) => { setRoutingKey(v); setResult(null); }} />
            <Input label="VHost" value={(window.QL.broker || {}).vhost || '/'} readOnly />
          </div>
        </Card>
        <Card title="Payload" subtitle="JSON is validated as you type; non-JSON publishes as text/plain." style={{ marginTop: 18 }}>
          <div style={{ position: 'relative', marginTop: 8 }}>
            <textarea value={payload} onChange={(e) => { setPayload(e.target.value); setResult(null); }} spellCheck={false}
              style={{ width: '100%', boxSizing: 'border-box', minHeight: 200, resize: 'vertical', padding: '12px 14px', fontFamily: 'var(--font-mono)', fontSize: 12.5, lineHeight: 1.7, color: 'var(--slate-700)', background: 'var(--slate-50)', border: '1px solid var(--border-default)', borderRadius: 'var(--radius-md)', outline: 'none' }} />
            <span style={{ position: 'absolute', top: 10, right: 12 }}>
              <Badge tone={validJson ? 'success' : 'warning'}>{validJson ? 'VALID JSON' : 'PLAIN TEXT'}</Badge>
            </span>
          </div>
          <div style={{ marginTop: 14 }}>
            <Switch checked={markTest} onChange={setMarkTest} label="Mark as test message" description="Adds x-queuelens-test: true so consumers can ignore it." />
          </div>
        </Card>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 18 }}>
          <Button icon="send" disabled={!canSend} onClick={send}>{busy ? 'Publishing…' : 'Publish Test Message'}</Button>
          {result && (result.ok
            ? <Alert tone="success" title={`Published to ${destination}`} style={{ flex: 1, padding: '8px 14px' }}>{result.text}</Alert>
            : <Alert tone="danger" title="Publish failed" style={{ flex: 1, padding: '8px 14px' }}>{result.text} Nothing was published — unroutable messages are never dropped silently.</Alert>)}
        </div>
        {result && result.ok && (
          <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
            <Button variant="secondary" iconRight="arrow-right" onClick={() => nav('audit')}>View in audit log</Button>
            <Button variant="secondary" onClick={() => nav('queues')}>Back to Queues</Button>
          </div>
        )}
      </div>
    );
  }

  window.QL.screens.Composer = Composer;
})();
