// QueueLens DS loader.
// Prefers the compiled design-system bundle (_ds_bundle.js) when present;
// otherwise synchronously evaluates the raw component sources so cards and
// UI kits work standalone. Exposes the namespace as window.__NS.
(function () {
  var FILES = [
    'components/icons/Icon.jsx',
    'components/actions/Button.jsx',
    'components/actions/IconButton.jsx',
    'components/indicators/Badge.jsx',
    'components/indicators/StatusPill.jsx',
    'components/indicators/Sparkline.jsx',
    'components/indicators/StatCard.jsx',
    'components/forms/Input.jsx',
    'components/forms/Select.jsx',
    'components/forms/SearchInput.jsx',
    'components/forms/Switch.jsx',
    'components/forms/Checkbox.jsx',
    'components/navigation/Tabs.jsx',
    'components/navigation/Stepper.jsx',
    'components/navigation/Pagination.jsx',
    'components/shell/SidebarNav.jsx',
    'components/shell/TopBar.jsx',
    'components/data/Alert.jsx',
    'components/data/DataTable.jsx',
    'components/data/KeyValue.jsx',
    'components/data/CodeBlock.jsx',
    'components/data/ActionChoiceCard.jsx',
  ];

  function findBundleNS() {
    for (var i = 0, ks = Object.keys(window); i < ks.length; i++) {
      try {
        var v = window[ks[i]];
        if (v && typeof v === 'object' && v.Button && v.Icon && v.StatusPill && v.DataTable) return v;
      } catch (e) {}
    }
    return null;
  }

  function fetchSync(url) {
    try {
      var x = new XMLHttpRequest();
      x.open('GET', url, false);
      x.send();
      return x.status >= 200 && x.status < 300 ? x.responseText : null;
    } catch (e) { return null; }
  }

  window.__loadDS = function (root) {
    var ns = findBundleNS();
    if (ns) return (window.__NS = ns);

    // Try the compiled bundle first.
    var bundle = fetchSync(root + '_ds_bundle.js');
    if (bundle) {
      try { new Function(bundle)(); } catch (e) {}
      ns = findBundleNS();
      if (ns) return (window.__NS = ns);
    }

    // Fallback: evaluate raw sources (plain React.createElement, no JSX).
    var NS = (window.__NS = {});
    FILES.forEach(function (f) {
      var src = fetchSync(root + f);
      if (!src) return;
      var importNames = [];
      src = src.replace(/import\s*\{([^}]*)\}\s*from\s*'[^']*';?/g, function (m, names) {
        names.split(',').forEach(function (n) { n = n.trim(); if (n) importNames.push(n); });
        return '';
      });
      src = src.replace(/import\s+React\s+from\s+'react';?/g, '');
      var exportsList = [];
      src = src.replace(/export\s+function\s+([A-Za-z0-9_]+)/g, function (m, n) { exportsList.push([n, n]); return 'function ' + n; });
      src = src.replace(/export\s*\{([^}]*)\};?/g, function (m, inner) {
        inner.split(',').forEach(function (p) {
          var mm = p.trim().split(/\s+as\s+/);
          if (mm[0]) exportsList.push([mm[0], mm[1] || mm[0]]);
        });
        return '';
      });
      var pre = importNames.length ? 'var ' + importNames.map(function (n) { return n + ' = NS.' + n; }).join(', ') + ';' : '';
      var post = exportsList.map(function (e) { return 'NS.' + e[1] + ' = ' + e[0] + ';'; }).join(' ');
      try {
        new Function('NS', 'React', pre + '\n' + src + '\n' + post)(NS, window.React);
      } catch (e) {
        console.error('ds-loader: failed to evaluate ' + f, e);
      }
    });
    return NS;
  };
})();
