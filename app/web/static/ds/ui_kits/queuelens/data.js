// QueueLens UI-kit data layer — wired to the real QueueLens API.
// Loaded synchronously before the screens (same pattern as ds-loader.js),
// so the kit renders live broker state instead of the design-time samples.
window.QL = window.QL || {};
window.QL.screens = window.QL.screens || {};

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
    if (action === 'publish') return 'publish';
    if (action === 'replay') {
      return (e.metadata && e.metadata.mode) === 'copy' ? 'replay_copy' : 'replay_move';
    }
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

  function mapXDeath(list) {
    return (list || []).map(function (d, i) {
      return { n: i + 1, reason: d.reason, queue: d.queue, count: d.count, time: d.time ? rel(d.time) : '—' };
    });
  }

  function mapMessage(m) {
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
      propsText: Object.keys(m.properties || {}).length
        ? JSON.stringify(m.properties, null, 2) : '(no properties)',
      xdeathList: mapXDeath(m.x_death),
      xdeathRaw: m.x_death || [],
    };
  }

  window.QL.fetchMessages = function (queue) {
    if (!queue) return [];
    var raw = (getJson('/api/queues/' + encodeURIComponent(queue) + '/messages') || {}).messages || [];
    return raw.map(mapMessage);
  };

  var exchangeCache = null;
  window.QL.fetchExchanges = function () {
    if (!exchangeCache) exchangeCache = (getJson('/api/exchanges') || {}).exchanges || [];
    return exchangeCache;
  };

  window.QL.fetchQueueInfo = function (name) {
    var result = getJson('/api/queues/' + encodeURIComponent(name));
    return result ? result.queue : null;
  };

  window.QL.fetchTopology = function () {
    return getJson('/api/topology') || { exchanges: [], bindings: [], queues: [] };
  };

  window.QL.fetchAlertRules = function () {
    var result = getJson('/api/alert-rules') || {};
    return { rules: result.rules || [], source: result.source || null };
  };

  // Messages currently held in parking queues, with park metadata from headers.
  window.QL.fetchParked = function () {
    var rows = [];
    var all = (getJson('/api/queues') || { queues: [] }).queues;
    all.filter(function (q) { return q.kind === 'parking'; }).forEach(function (q) {
      var msgs = (getJson('/api/queues/' + encodeURIComponent(q.name) + '/messages') || {}).messages || [];
      msgs.forEach(function (m) {
        var h = m.headers || {};
        rows.push({
          id: m.message_id || m.fingerprint.slice(0, 12),
          fingerprint: m.fingerprint,
          parkingQueue: q.name,
          source: h['x-queuelens-source-queue'] || q.name.replace(/\.parking$/, ''),
          by: h['x-queuelens-replayed-by'] || h['x-queuelens-user'] || '\u2014',
          at: h['x-queuelens-parked-at'] ? String(h['x-queuelens-parked-at']).slice(0, 19).replace('T', ' ') : '\u2014',
          age: h['x-queuelens-parked-at'] ? rel(String(h['x-queuelens-parked-at'])) : '\u2014',
          type: (m.payload_format || 'json').toUpperCase(),
          size: human(m.payload_size),
          msg: mapMessage(m),
        });
      });
    });
    return rows;
  };

  window.QL.fetchConfig = function () { return getJson('/api/config') || {}; };
  window.QL.config = window.QL.fetchConfig();
  window.QL.testConnection = function () { return getJson('/api/broker/test'); };

  // XHR rather than fetch: fetch() rejects relative URLs when the page URL carries
  // basic-auth credentials (http://user:pass@host/), a common way to open QueueLens.
  window.QL.requestJson = function (method, path, body) {
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest();
      x.open(method, path);
      x.setRequestHeader('Content-Type', 'application/json');
      x.onload = function () {
        var detail = {};
        try { detail = JSON.parse(x.responseText); } catch (e) {}
        if (x.status >= 200 && x.status < 300) resolve(detail);
        else reject(new Error(detail.detail || ('HTTP ' + x.status)));
      };
      x.onerror = function () { reject(new Error('Network error')); };
      x.send(body === undefined ? null : JSON.stringify(body));
    });
  };
  window.QL.postJson = function (path, body) { return window.QL.requestJson('POST', path, body); };
  window.QL.putJson = function (path, body) { return window.QL.requestJson('PUT', path, body); };

  window.QL.fetchServerSettings = function () { return getJson('/api/settings') || {}; };
  window.QL.serverSettings = window.QL.fetchServerSettings();
  window.QL.saveSettings = function (values) {
    return window.QL.putJson('/api/settings', { values: values });
  };
  window.QL.fetchAlerts = function () { return (getJson('/api/alerts') || {}).rules || []; };
  window.QL.fetchNotificationsStored = function () {
    return (getJson('/api/notifications') || {}).notifications || [];
  };
  window.QL.fetchEnvironments = function () {
    return (getJson('/api/environments') || {}).environments || [];
  };

  var messagesRaw = defaultQueue
    ? ((getJson('/api/queues/' + encodeURIComponent(defaultQueue) + '/messages') || {}).messages || [])
    : [];
  var messages = messagesRaw.map(mapMessage);

  var first = messagesRaw[0];
  var payload = first ? JSON.stringify(first.payload, null, 2) : '{}';
  var xdeath = mapXDeath(first && first.x_death);

  window.QL.fetchAudit = function () {
    var raw = (getJson('/api/audit?limit=500') || {}).events || [];
    return raw.filter(function (e) { return e.result !== 'started'; }).map(function (e, i) {
      var meta = e.metadata || {};
      return {
        key: (e.id || '') + '-' + i,
        time: e.timestamp ? e.timestamp.slice(0, 19).replace('T', ' ') : '—',
        user: e.username, action: kitAction(e), queue: e.source_queue || '—',
        target: target(e), result: kitResult(e.result),
        duration: meta.duration_ms != null ? (meta.duration_ms / 1000).toFixed(2) + 's' : '—',
        fingerprint: e.message_fingerprint || null,
        headersAdded: meta.headers_added || null,
        xdeathList: mapXDeath(meta.x_death),
        error: e.error_message || meta.error || null,
      };
    });
  };
  var audit = window.QL.fetchAudit();

  var recentActions = audit.slice(0, 5).map(function (r) {
    return {
      time: r.time.length > 11 ? r.time.slice(11) : r.time,
      action: r.action, label: title(r.action),
      queue: r.queue, target: r.target, result: r.result,
    };
  });

  var notifications = [];
  var LEVELS = { Alert: 1, Warning: 1, Info: 1, Success: 1 };
  window.QL.fetchNotificationsStored().forEach(function (n) {
    notifications.push({
      time: n.timestamp ? rel(n.timestamp) : '\u2014',
      level: LEVELS[n.level] ? n.level : 'Info',
      title: n.title, message: n.message, source: n.source || 'Alert Engine',
      delivery: n.delivery || {},
    });
  });
  queues.forEach(function (q) {
    if (q.status === 'attention') {
      notifications.push({ time: 'now', level: 'Warning', title: 'DLQ queue needs attention',
        message: q.name + ' has ' + q.messages + ' messages', source: 'Queue Monitor' });
    }
  });
  audit.slice(0, 8).forEach(function (r) {
    var failed = r.result === 'Failed';
    notifications.push({
      time: r.time.length > 11 ? r.time.slice(11) : r.time,
      level: failed ? 'Alert' : 'Success',
      title: failed ? 'Failed ' + title(r.action) + ' action' : title(r.action) + ' action successful',
      message: r.queue + (r.target !== '—' ? ' → ' + r.target : ''),
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
