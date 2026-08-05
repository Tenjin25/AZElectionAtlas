const test = require('node:test');
const assert = require('node:assert/strict');

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
