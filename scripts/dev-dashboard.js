import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { spawn } from 'child_process';

const __dirname = dirname(fileURLToPath(import.meta.url));
const dashboardDir = resolve(__dirname, '..', 'dashboard-ui');

const child = spawn('node', ['node_modules/vite/bin/vite.js', '--host'], {
  cwd: dashboardDir,
  stdio: 'inherit',
  shell: true,
});

child.on('exit', (code) => process.exit(code));
