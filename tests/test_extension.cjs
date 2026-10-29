// Exercise Firefox background handlers without touching a real browser profile.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const handlers = {};
const messages = [];
const notifications = [];
let selection = 'Highlighted browser text';
const browser = {
  runtime: {
    sendNativeMessage: async (host, message) => { assert.equal(host, 'org.runespeak.lite'); messages.push(message); return {ok: true}; },
    getURL: path => path,
    onMessage: {addListener: fn => handlers.message = fn}
  },
  browserAction: {setBadgeText: async () => {}, setTitle: async () => {}},
  notifications: {create: async message => notifications.push(message)},
  tabs: {query: async () => [{id: 1, url: 'https://example.com/private'}], executeScript: async () => [selection]},
  contextMenus: {create: item => assert.deepEqual(item.contexts, ['selection']), onClicked: {addListener: fn => handlers.menu = fn}},
  commands: {onCommand: {addListener: fn => handlers.command = fn}}
};
// Use the same JS realm so deepEqual does not reject cross-realm prototypes.
global.browser = browser;
vm.runInThisContext(fs.readFileSync('firefox-extension/background.js', 'utf8'));
(async () => {
  await handlers.message({action: 'read-selection'});
  assert.deepEqual(messages.pop(), {action: 'read', text: selection, source: 'https://example.com/private'});
  await handlers.message({action: 'pause'});
  assert.deepEqual(messages.pop(), {action: 'pause'});
  handlers.menu({menuItemId: 'read-selection', selectionText: 'Frame selection', pageUrl: 'https://example.com/frame'});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(messages.pop().text, 'Frame selection');
  selection = '';
  await assert.rejects(handlers.message({action: 'read-selection'}), /Highlight some text/);
  browser.runtime.sendNativeMessage = async () => ({ok: false, error: 'Open Runespeak Lite first.'});
  await assert.rejects(handlers.message({action: 'resume'}), /Open Runespeak/);
  handlers.menu({menuItemId: 'read-selection', selectionText: 'Text'});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(notifications.length, 1);
  assert.match(notifications[0].message, /Open Runespeak/);
  console.log('Firefox selection, menu, playback command and error tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
