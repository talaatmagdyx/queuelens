// Login screen — dark promo panel left, sign-in form right.
(function () {
  const { Icon, Button, Input, Checkbox } = window.__NS;

  function Login({ onSignIn, onSetup }) {
    const [remember, setRemember] = React.useState(false);
    return (
      <div style={{ display: 'flex', minHeight: '100vh', background: 'var(--slate-50)', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--font-ui)' }}>
        <div style={{ display: 'flex', width: 880, minHeight: 520, background: 'var(--surface-card)', border: '1px solid var(--border-default)', borderRadius: 14, overflow: 'hidden', boxShadow: 'var(--shadow-popover)' }}>
          <div style={{ width: 400, flex: 'none', background: 'linear-gradient(160deg, #0B1430 0%, #060B1D 100%)', color: '#fff', padding: 36, display: 'flex', flexDirection: 'column', boxSizing: 'border-box' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 38, height: 38, borderRadius: 10, background: 'var(--blue-600)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Icon name="scan-eye" size={20} color="#fff" />
              </div>
              <div style={{ fontSize: 21, fontWeight: 700 }}>Queue<span style={{ color: 'var(--blue-500)' }}>Lens</span></div>
            </div>
            <div style={{ marginTop: 22, fontSize: 15, fontWeight: 600 }}>Safe. Observable. Reliable.</div>
            <div style={{ marginTop: 8, fontSize: 13.5, color: 'rgba(255,255,255,.65)', lineHeight: 1.6 }}>
              Browse, inspect, and recover RabbitMQ dead-letter messages with confidence.
            </div>
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative', marginTop: 20 }}>
              <div style={{ width: 240, height: 170, borderRadius: 12, background: 'rgba(46,124,240,.08)', border: '1px solid rgba(46,124,240,.25)', padding: 16, boxSizing: 'border-box', display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ width: 90, height: 8, borderRadius: 4, background: 'var(--blue-600)' }}></div>
                <div style={{ width: 150, height: 6, borderRadius: 3, background: 'rgba(255,255,255,.18)' }}></div>
                <div style={{ width: 120, height: 6, borderRadius: 3, background: 'rgba(255,255,255,.12)' }}></div>
                <div style={{ width: 160, height: 6, borderRadius: 3, background: 'rgba(255,255,255,.12)' }}></div>
                <div style={{ display: 'flex', gap: 8, marginTop: 'auto' }}>
                  <div style={{ width: 34, height: 22, borderRadius: 4, background: 'rgba(46,124,240,.4)' }}></div>
                  <div style={{ width: 34, height: 30, borderRadius: 4, background: 'rgba(46,124,240,.55)', marginTop: -8 }}></div>
                  <div style={{ width: 34, height: 16, borderRadius: 4, background: 'rgba(46,124,240,.3)', marginTop: 6 }}></div>
                </div>
              </div>
              <div style={{ position: 'absolute', right: 28, top: '38%', width: 54, height: 54, borderRadius: 999, background: 'var(--blue-600)', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 0 0 8px rgba(46,124,240,.18)' }}>
                <Icon name="shield-check" size={26} color="#fff" />
              </div>
            </div>
          </div>
          <div style={{ flex: 1, padding: '48px 52px', boxSizing: 'border-box', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--text-heading)' }}>Welcome back</div>
            <div style={{ fontSize: 14, color: 'var(--text-body)', marginTop: 4, marginBottom: 26 }}>Sign in to your QueueLens account</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <Input label="Username" placeholder="Enter your username" />
              <Input label="Password" type="password" placeholder="Enter your password" />
              <div style={{ display: 'flex', alignItems: 'center' }}>
                <Checkbox checked={remember} onChange={setRemember} label="Remember me" />
                <a href="#" onClick={(e) => e.preventDefault()} style={{ marginLeft: 'auto', fontSize: 13, fontWeight: 600, color: 'var(--text-link)', textDecoration: 'none' }}>Forgot password?</a>
              </div>
              <Button size="lg" onClick={() => onSignIn('Admin')} style={{ width: '100%' }}>Sign in</Button>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 12, color: 'var(--slate-400)' }}>Demo roles:</span>
                {['Admin', 'Operator', 'Viewer'].map((r) => (
                  <Button key={r} size="sm" variant="secondary" onClick={() => onSignIn(r)}>{r}</Button>
                ))}
              </div>
            </div>
            <div style={{ textAlign: 'center', marginTop: 34, fontSize: 12.5, color: 'var(--slate-400)' }}>
              <a href="#" onClick={(e) => { e.preventDefault(); onSetup && onSetup(); }} style={{ color: 'var(--text-link)', fontWeight: 600, textDecoration: 'none' }}>First time here? Set up QueueLens</a><br />
              QueueLens is open source · v0.4.0
            </div>
          </div>
        </div>
      </div>
    );
  }

  window.QL.screens = window.QL.screens || {};
  window.QL.screens.Login = Login;
})();
