// QueueLens UI-kit data layer — wired to the real QueueLens API.
// Loaded synchronously before the screens (same pattern as ds-loader.js),
// so the kit renders live broker state instead of the design-time samples.
window.QL = window.QL || {};

(function () {
  function getJson(url) {
    try {
      var x = new XMLHttpRequest();
      x.open('GET', url, false);
      x.setRequestHeader('Accept', 'application/json');
      x.send();
      return x.status >= 200 && x.status < 300 ? JSON.parse(x.responseText) : null;
    } catch (e) { return null; }
  }

  function rel(iso) {
    if (!iso) return '—';
    var then = Date.parse(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
    if (isNaN(then)) return '—';
    var m = Math.round((Date.now() - then) / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return m + 'm ago';
    if (m < 1440) return Math.round(m / 60) + 'h ago';
    return Math.round(m / 1440) + 'd ago';
  }

  function human(bytes) {
    if (bytes == null) return '—';
    if (bytes < 1024) return bytes + ' B';
    return (bytes / 1024).toFixed(2) + ' KB';
  }

  function title(s) {
    return (s || '').replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  function target(e) {
    return e.target_queue || (e.target_exchange
      ? e.target_exchange + ' / ' + (e.target_routing_key || '') : '—');
  }

  var t0 = performance.now();
  var broker = getJson('/api/broker') || {};
  var latency = Math.round(performance.now() - t0);
  var ready = getJson('/ready');

  // Normalize to the kit's vocabularies (unknown keys crash its lookup maps).
  var STATUS_ALIAS = { warning: 'attention', empty: 'idle' };
  function kitResult(result) {
    if (result === 'success') return 'Success';
    if (result === 'failed' || result === 'partial') return 'Failed';
    return 'Started';
  }
  function kitAction(e) {
    var stamped = e.metadata && e.metadata.headers_added
      && e.metadata.headers_added['x-queuelens-action'];
    if (stamped === 'replay_move' || stamped === 'replay_copy') return stamped;
    var action = (e.action || '').replace(/^bulk_/, '');
    if (action === 'replay') return 'replay_move';
    return action === 'park' || action === 'delete' ? action : 'replay_move';
  }

  var queuesRaw = (getJson('/api/queues') || {}).queues || [];
  var queues = queuesRaw.map(function (q) {
    var type = q.kind === 'parking' ? 'PARKING' : q.kind === 'normal' ? 'NORMAL' : 'DLQ';
    var row = {
      name: q.name, type: type, messages: q.messages, ready: q.messages_ready,
      unacked: q.messages_unacked, consumers: q.consumers,
      status: STATUS_ALIAS[q.status] || q.status,
      qtype: q.queue_type,
      rate: q.publish_rate != null ? q.publish_rate : null,
      last: q.idle_since ? rel(q.idle_since.replace(' ', 'T')) : '—',
    };
    if (q.kind === 'retry') row.retry = true;
    return row;
  });

  var dlqs = queuesRaw.filter(function (q) { return q.is_dlq; });
  var defaultQueue = dlqs.length ? dlqs[0].name : (queuesRaw[0] ? queuesRaw[0].name : '');
  window.QL.defaultQueue = defaultQueue;

  var messagesRaw = defaultQueue
    ? ((getJson('/api/queues/' + encodeURIComponent(defaultQueue) + '/messages') || {}).messages || [])
    : [];
  function mapXDeath(list) {
    return (list || []).map(function (d, i) {
      return { n: i + 1, reason: d.reason, queue: d.queue, count: d.count, time: d.time ? rel(d.time) : '—' };
    });
  }

  var messages = messagesRaw.map(function (m) {
    return {
      id: m.message_id || m.fingerprint.slice(0, 12),
      fingerprint: m.fingerprint, queue: m.queue,
      at: m.timestamp ? m.timestamp.slice(0, 19).replace('T', ' ') : '—',
      type: (m.payload_format || 'json').toUpperCase(),
      size: human(m.payload_size),
      xdeath: (m.x_death || []).length,
      preview: JSON.stringify(m.payload).slice(0, 30) + '…',
      payloadText: typeof m.payload === 'string' ? m.payload : JSON.stringify(m.payload, null, 2),
      headersText: Object.keys(m.headers || {}).length ? JSON.stringify(m.headers, null, 2) : '(no headers)',
      xdeathList: mapXDeath(m.x_death),
      xdeathRaw: m.x_death || [],
    };
  });

  var first = messagesRaw[0];
  var payload = first ? JSON.stringify(first.payload, null, 2) : '{}';
  var xdeath = mapXDeath(first && first.x_death);

  var auditRaw = (getJson('/api/audit?limit=100') || {}).events || [];
  var outcomes = auditRaw.filter(function (e) { return e.result !== 'started'; });
  var audit = outcomes.map(function (e) {
    return {
      time: e.timestamp ? e.timestamp.slice(0, 19).replace('T', ' ') : '—',
      user: e.username, action: kitAction(e), queue: e.source_queue || '—',
      target: target(e), result: kitResult(e.result),
      duration: e.metadata && e.metadata.duration_ms != null
        ? (e.metadata.duration_ms / 1000).toFixed(2) + 's' : '—',
    };
  });

  var recentActions = outcomes.slice(0, 5).map(function (e) {
    var action = kitAction(e);
    return {
      time: e.timestamp ? e.timestamp.slice(11, 19) : '—',
      action: action, label: title(action),
      queue: e.source_queue || '—', target: target(e), result: kitResult(e.result),
    };
  });

  var notifications = [];
  queues.forEach(function (q) {
    if (q.status === 'attention') {
      notifications.push({ time: 'now', level: 'Warning', title: 'DLQ queue needs attention',
        message: q.name + ' has ' + q.messages + ' messages', source: 'Queue Monitor' });
    }
  });
  auditRaw.filter(function (e) { return e.result !== 'started'; }).slice(0, 8).forEach(function (e) {
    var failed = e.result === 'failed' || e.result === 'partial';
    notifications.push({
      time: e.timestamp ? rel(e.timestamp) : '—',
      level: failed ? 'Alert' : 'Success',
      title: failed ? 'Failed ' + e.action + ' action' : title(e.action) + ' action successful',
      message: (e.source_queue || '—') + (target(e) !== '—' ? ' → ' + target(e) : ''),
      source: 'Audit Log',
    });
  });
  window.QL.alertCount = notifications.filter(function (n) {
    return n.level === 'Alert' || n.level === 'Warning';
  }).length;

  var accounts = (getJson('/api/users') || {}).accounts || [];
  var users = accounts.map(function (a) {
    return {
      name: a.username, email: '—',
      role: a.role === 'Administrator' ? 'Admin' : 'Operator',
      envs: [broker.environment || 'development'], last: '—', status: 'Active',
    };
  });
  window.QL.user = (accounts.find(function (a) { return a.role === 'Administrator'; }) || accounts[0] || { username: 'admin' }).username;

  window.QL.broker = {
    environment: broker.environment || 'development',
    host: broker.host || 'rabbitmq:5672',
    api: 'RabbitMQ ' + (broker.rabbitmq_version || '—'),
    vhost: broker.vhost || '/',
    live: !!(ready && ready.status === 'ok'),
    latency: latency + 'ms',
  };

  window.QL.data = {
    queues: queues,
    payload: payload,
    messages: messages,
    xdeath: xdeath,
    audit: audit,
    notifications: notifications,
    recentActions: recentActions,
    users: users,
  };
})();
