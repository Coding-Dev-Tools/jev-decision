// Build, pack and install outside the checkout. Retain artifacts; never publish.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const npm = process.env.npm_execpath;
assert(npm && fs.existsSync(npm), 'Run through npm run check:package');
function run(args, cwd) {
  return execFileSync(process.execPath, [npm, ...args], {cwd, encoding:'utf8'});
}
run(['run', 'build'], root);
const packed = JSON.parse(run(['pack', '--json'], root))[0];
assert(packed.files.some(file => file.path === 'LICENSE'));
assert(packed.files.some(file => file.path === 'dist/index.d.ts'));
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'jev-npm-'));
fs.writeFileSync(path.join(directory, 'package.json'), JSON.stringify({name:'jev-package-smoke',version:'1.0.0',private:true}));
run(['install', '--ignore-scripts', '--no-audit', '--no-fund', path.join(root, packed.filename)], directory);
const {JevClient} = require(path.join(directory, 'node_modules/@coding-dev-tools/jev-decision'));
new JevClient({offlineMode:true}).evaluate('sample', {x:{type:'noul',instructions:'Is this a sample?'}}).then(result => {
  assert.equal(result.status, 'offline');
  assert.deepEqual(result.decisions, {});
  assert.equal(result.attempts, 0);
  console.log(JSON.stringify({artifact:packed.filename, cleanInstall:true, providerCalls:0}));
});
