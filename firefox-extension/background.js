/* Native messages go only to the local, explicitly installed companion host. */
const HOST = 'org.runespeak.lite';

async function send(message) {
  const response = await browser.runtime.sendNativeMessage(HOST, message);
  if (!response || !response.ok) throw new Error(response?.error || 'Runespeak did not accept the command.');
  await browser.browserAction.setBadgeText({text: ''});
  return response;
}

async function readSelection() {
  const [tab] = await browser.tabs.query({active: true, currentWindow: true});
  if (!tab) throw new Error('Open a webpage and highlight some text first.');
  const selections = await browser.tabs.executeScript(tab.id, {
    code: `(() => {
      const element = document.activeElement;
      if (element && /^(TEXTAREA|INPUT)$/.test(element.tagName)) {
        if (element.type === 'password') return '';
        if (typeof element.selectionStart === 'number')
          return element.value.slice(element.selectionStart, element.selectionEnd);
      }
      return window.getSelection().toString();
    })()`
  });
  const text = selections.find(value => value && value.trim());
  if (!text) throw new Error('Highlight some text first, or use the selection right-click menu.');
  return send({action: 'read', text, source: tab.url || ''});
}

async function report(error) {
  const message = error.message || String(error);
  await browser.browserAction.setBadgeText({text: '!'});
  await browser.browserAction.setTitle({title: `Runespeak: ${message}`});
  await browser.notifications.create({type: 'basic', iconUrl: browser.runtime.getURL('icon.svg'), title: 'Runespeak Lite', message});
}

browser.contextMenus.create({id: 'read-selection', title: 'Read selection with Runespeak', contexts: ['selection']});
browser.contextMenus.onClicked.addListener((info) => {
  if (info.menuItemId === 'read-selection') {
    send({action: 'read', text: info.selectionText, source: info.pageUrl || ''}).catch(report);
  }
});
browser.commands.onCommand.addListener(command => {
  if (command === 'read-selection') readSelection().catch(report);
});
browser.runtime.onMessage.addListener(message => {
  if (message.action === 'read-selection') return readSelection();
  if (['pause', 'resume', 'stop', 'restart'].includes(message.action)) return send(message);
  return Promise.reject(new Error('Unknown Runespeak command'));
});
