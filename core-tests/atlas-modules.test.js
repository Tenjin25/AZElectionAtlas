const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const modules = [
  'atlas-classification', 'atlas-core', 'atlas-data', 'atlas-display', 'atlas-election',
  'atlas-manifest', 'atlas-modeling', 'atlas-regions', 'atlas-trends', 'atlas-turnout', 'atlas-url-state'
];

for (const name of modules) {
  test(`${name} exposes a module API`, () => {
    const api = require(`../js/${name}.js`);
    assert.equal(typeof api, 'object');
    assert.ok(Object.keys(api).length > 0);
  });
}

test('index loads every required atlas module before the inline application', () => {
  const indexPath = path.join(__dirname, '..', 'index.html');
  const html = fs.readFileSync(indexPath, 'utf8');
  const inlineAppMatch = /<script>\r?\n\s*\/\/ --- CONFIG ---/.exec(html);
  const inlineAppIndex = inlineAppMatch?.index ?? -1;

  assert.notEqual(inlineAppIndex, -1, 'inline application script is present');
  for (const name of modules.filter(name => name !== 'atlas-modeling')) {
    const pattern = new RegExp(`<script\\s+src=["']\\./js/${name}\\.js(?:\\?[^"']*)?["']><\\/script>`);
    const match = pattern.exec(html);
    assert.ok(match, `index includes a valid script tag for ${name}`);
    assert.ok(match.index < inlineAppIndex, `${name} loads before the inline application`);
  }
});
