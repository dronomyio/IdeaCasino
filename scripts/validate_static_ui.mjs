import { readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';

const html = readFileSync(new URL('../app/static/index.html', import.meta.url), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]);
if (scripts.length !== 1) {
  throw new Error(`Expected exactly one inline application script; found ${scripts.length}.`);
}
const temporaryScript = join(tmpdir(), `idea-casino-static-ui-${process.pid}.js`);
try {
  writeFileSync(temporaryScript, scripts[0]);
  execFileSync(process.execPath, ['--check', temporaryScript], { stdio: 'inherit' });
  console.log('Static reviewed-evidence UI syntax validation passed.');
} finally {
  try { unlinkSync(temporaryScript); } catch { /* Temporary file was not created. */ }
}
