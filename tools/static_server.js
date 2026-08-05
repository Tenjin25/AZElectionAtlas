const http = require('http');
const fs = require('fs');
const path = require('path');
const { URL } = require('url');

function arg(argv, name, fallback) {
  const index = argv.indexOf(name);
  const value = argv[index + 1];
  return index >= 0 && value && !value.startsWith('--') ? value : fallback;
}

const argv = process.argv.slice(2);
const port = Number(arg(argv, '--port', '4173'));
const host = arg(argv, '--host', '127.0.0.1');
const rootDir = path.resolve(process.cwd());
const mime = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.geojson': 'application/geo+json; charset=utf-8', '.csv': 'text/csv; charset=utf-8',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp'
};

function resolveSafe(requestPath) {
  const decoded = decodeURIComponent(String(requestPath || '').replace(/\0/g, ''));
  const clean = decoded.split('?')[0].split('#')[0];
  const resolved = path.resolve(rootDir, `.${clean}`);
  return resolved === rootDir || resolved.startsWith(`${rootDir}${path.sep}`) ? resolved : null;
}

const server = http.createServer((request, response) => {
  try {
    let filePath = resolveSafe(new URL(request.url || '/', `http://${host}:${port}`).pathname);
    if (!filePath) return response.writeHead(400).end('Bad request');
    if (fs.existsSync(filePath) && fs.statSync(filePath).isDirectory()) filePath = path.join(filePath, 'index.html');
    fs.readFile(filePath, (error, data) => {
      if (error) return response.writeHead(404).end('Not found');
      response.writeHead(200, { 'Content-Type': mime[path.extname(filePath).toLowerCase()] || 'application/octet-stream', 'Cache-Control': 'no-store' });
      response.end(data);
    });
  } catch {
    response.writeHead(500).end('Server error');
  }
});

server.listen(port, host, () => console.log(`Static server listening on http://${host}:${port}`));
