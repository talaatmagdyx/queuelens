// Precompile the SPA for production images: JSX → plain JS at build time so the
// browser never runs Babel (faster first paint, CSP-compatible, ~3MB lighter).
// Usage: node scripts/build_frontend.mjs <static-dir>
import { transformSync } from '@babel/core';
import fs from 'node:fs';
import path from 'node:path';

const root = process.argv[2];
if (!root) throw new Error('usage: build_frontend.mjs <static-dir>');
const kit = path.join(root, 'ds', 'ui_kits', 'queuelens');

const compile = (code) =>
  transformSync(code, { presets: [['@babel/preset-react', { runtime: 'classic' }]] }).code;

let compiled = 0;
for (const file of fs.readdirSync(kit)) {
  if (!file.endsWith('.jsx')) continue;
  const source = fs.readFileSync(path.join(kit, file), 'utf8');
  const out = file.replace(/\.jsx$/, '.compiled.js');
  fs.writeFileSync(path.join(kit, out), compile(source));
  compiled += 1;
}

let html = fs.readFileSync(path.join(kit, 'index.html'), 'utf8');
// Babel standalone is no longer needed at runtime.
html = html.replace(/<script src="\/static\/vendor\/babel\.min\.js"><\/script>\s*\n?/, '');
// External JSX scripts → their compiled counterparts.
html = html.replace(
  /<script type="text\/babel" src="([^"]+)\.jsx(\?v=[^"]*)?"><\/script>/g,
  '<script src="$1.compiled.js$2"></script>'
);
// The inline app shell is JSX too — compile it in place.
html = html.replace(/<script type="text\/babel">([\s\S]*?)<\/script>/, (_match, code) => {
  return '<script>\n' + compile(code) + '\n</script>';
});
fs.writeFileSync(path.join(kit, 'index.html'), html);
console.log(`frontend precompiled: ${compiled} kit files + inline shell, babel removed`);
