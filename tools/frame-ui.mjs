#!/usr/bin/env node
// Node 22+: intentionally dependency-free. Operates only on the rendered DOM.
import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const usage = `Usage: node tools/frame-ui.mjs <command> [options]
  list                                  List CDP targets as JSON
  inspect --target ID                   Inventory rendered text and controls
  screenshot --target ID --output PATH  Save the current viewport as PNG
  click --target ID --label TEXT        Click one exact visible control label
Options:
  --endpoint URL  CDP HTTP endpoint (default http://127.0.0.1:9222)
  --timeout MS    Request timeout (default 10000)
  --help          Show this help

Start a tunnel in another terminal:
  ssh -N -L 9222:127.0.0.1:8080 steamos@frame.local
Target IDs can change when Steam restarts. Run list before selecting a target.`;

function parse(args) {
  if (!args.length || args.includes('--help')) return null;
  const [command, ...rest] = args;
  if (!['list', 'inspect', 'screenshot', 'click'].includes(command)) {
    throw new Error(`Unknown command: ${command}. Use --help.`);
  }
  const options = { command, endpoint: 'http://127.0.0.1:9222', timeout: 10000 };
  const allowed = new Set(['endpoint', 'timeout', ...(command === 'list' ? [] : ['target']),
    ...(command === 'screenshot' ? ['output'] : []), ...(command === 'click' ? ['label'] : [])]);
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i].replace(/^--/, '');
    if (!rest[i].startsWith('--') || !allowed.has(key) || rest[i + 1] === undefined) {
      throw new Error(`Invalid or incomplete option: ${rest[i]}. Use --help.`);
    }
    options[key] = rest[i + 1];
  }
  for (const key of command === 'list' ? [] : ['target', ...(command === 'screenshot' ? ['output'] : []),
    ...(command === 'click' ? ['label'] : [])]) {
    if (!options[key]?.trim()) throw new Error(`The ${command} command requires --${key}.`);
  }
  options.timeout = Number(options.timeout);
  if (!Number.isSafeInteger(options.timeout) || options.timeout < 1 || options.timeout > 300000) {
    throw new Error('--timeout must be an integer between 1 and 300000 milliseconds.');
  }
  const endpoint = new URL(options.endpoint);
  if (!['http:', 'https:'].includes(endpoint.protocol)) throw new Error('--endpoint must use HTTP or HTTPS.');
  return options;
}

async function targets({ endpoint, timeout }) {
  let response;
  try {
    response = await fetch(new URL('/json/list', endpoint), { signal: AbortSignal.timeout(timeout) });
  } catch (error) {
    throw new Error(`Cannot reach CDP at ${endpoint}: ${error.message}. Check the SSH tunnel.`);
  }
  if (!response.ok) throw new Error(`CDP target listing returned HTTP ${response.status}.`);
  const result = await response.json();
  if (!Array.isArray(result)) throw new Error('CDP /json/list did not return a target array.');
  return result;
}

async function connect(url, timeout) {
  const socket = new WebSocket(url);
  const pending = new Map();
  let sequence = 0;
  const rejectPending = (error) => {
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(error);
    }
    pending.clear();
  };
  socket.addEventListener('message', ({ data }) => {
    let message;
    try { message = JSON.parse(data); } catch { return; }
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id);
    clearTimeout(request.timer);
    if (message.error) request.reject(new Error(`${request.method}: ${message.error.message}`));
    else request.resolve(message.result);
  });
  socket.addEventListener('close', () => rejectPending(new Error('CDP connection closed. Run list to refresh target IDs.')));
  socket.addEventListener('error', () => rejectPending(new Error('CDP WebSocket connection failed.')));
  try {
    await new Promise((resolveOpen, reject) => {
      const timer = setTimeout(() => reject(new Error(`CDP WebSocket connection timed out after ${timeout} ms.`)), timeout);
      const finish = (callback, value) => { clearTimeout(timer); callback(value); };
      socket.addEventListener('open', () => finish(resolveOpen), { once: true });
      socket.addEventListener('error', () => finish(reject, new Error('Cannot open CDP WebSocket. Check the tunnel and target ID.')), { once: true });
      socket.addEventListener('close', () => finish(reject, new Error('CDP WebSocket closed before connecting.')), { once: true });
    });
  } catch (error) {
    socket.close();
    throw error;
  }
  return {
    call(method, params = {}) {
      if (socket.readyState !== WebSocket.OPEN) return Promise.reject(new Error('CDP connection is not open.'));
      const id = ++sequence;
      return new Promise((resolveCall, reject) => {
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`${method} timed out after ${timeout} ms.`));
        }, timeout);
        pending.set(id, { resolve: resolveCall, reject, timer, method });
        try { socket.send(JSON.stringify({ id, method, params })); }
        catch (error) { clearTimeout(timer); pending.delete(id); reject(error); }
      });
    },
    close() { socket.close(); rejectPending(new Error('CDP connection closed by client.')); },
  };
}

// This function is serialized into the selected page. Read only DOM/layout;
// do not access framework internals, application stores, or hidden state.
function renderedDOM(label) {
  const normalize = (text) => String(text ?? '').replace(/\s+/g, ' ').trim();
  const visible = (element) => {
    if (!element || !element.getClientRects().length) return false;
    for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      if (style.display === 'none' || style.visibility !== 'visible' || Number(style.opacity) === 0) return false;
    }
    return [...element.getClientRects()].some((r) => r.width > 0 && r.height > 0 &&
      r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth);
  };
  const controlLabel = (element) => normalize(element.matches('input') ? element.value : element.innerText);
  const controls = [...document.querySelectorAll('button, a[href], input[type="button"], input[type="submit"], input[type="reset"], [role="button"], [role="menuitem"], [role="tab"], [role="link"], [role="checkbox"], [role="switch"], [role="option"], [tabindex], summary')]
    .filter(visible);
  const describe = (element) => ({
    tag: element.tagName.toLowerCase(), role: element.getAttribute('role'),
    label: controlLabel(element), disabled: element.matches(':disabled') || element.getAttribute('aria-disabled') === 'true',
  });
  if (label !== undefined) {
    const matches = controls.filter((element) => controlLabel(element) === normalize(label));
    if (matches.length !== 1) return { error: `Expected one visible control labeled ${JSON.stringify(label)}; found ${matches.length}. Run inspect and select a unique label.` };
    const element = matches[0];
    if (describe(element).disabled) return { error: `Control ${JSON.stringify(label)} is disabled.` };
    const rect = element.getBoundingClientRect();
    const x = (Math.max(0, rect.left) + Math.min(innerWidth, rect.right)) / 2;
    const y = (Math.max(0, rect.top) + Math.min(innerHeight, rect.bottom)) / 2;
    const hit = document.elementFromPoint(x, y);
    if (!hit || !(hit === element || element.contains(hit))) return { error: `Control ${JSON.stringify(label)} is covered at its click point.` };
    return { x, y, control: describe(element) };
  }
  const text = [];
  const seen = new Set();
  const walker = document.createTreeWalker(document.body ?? document.documentElement, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (!visible(node.parentElement) || node.parentElement.closest('script, style, template')) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    if (![...range.getClientRects()].some((r) => r.width > 0 && r.height > 0 && r.bottom > 0 &&
      r.right > 0 && r.top < innerHeight && r.left < innerWidth)) continue;
    const value = normalize(node.textContent);
    if (value && !seen.has(value)) { seen.add(value); text.push(value); }
  }
  return { title: document.title, url: location.href, viewport: { width: innerWidth, height: innerHeight },
    controls: controls.map(describe), text };
}

async function evaluate(client, label) {
  const result = await client.call('Runtime.evaluate', {
    expression: `(${renderedDOM.toString()})(${label === undefined ? '' : JSON.stringify(label)})`,
    returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(`DOM inspection failed: ${result.exceptionDetails.exception?.description ?? result.exceptionDetails.text}`);
  if (result.result?.value === undefined) throw new Error('DOM inspection returned no value.');
  const value = result.result.value;
  if (value.error) throw new Error(value.error);
  return value;
}

async function main() {
  const options = parse(process.argv.slice(2));
  if (!options) { console.log(usage); return; }
  if (typeof WebSocket !== 'function') throw new Error('This helper requires Node.js 22 or later with built-in WebSocket.');
  const pages = await targets(options);
  if (options.command === 'list') {
    console.log(JSON.stringify(pages.map(({ id, type, title, url }) => ({ id, type, title, url })), null, 2));
    return;
  }
  const page = pages.find(({ id }) => id === options.target);
  if (!page) throw new Error(`Target ${options.target} is not present. Run list to get current target IDs.`);
  if (!page.webSocketDebuggerUrl) throw new Error(`Target ${options.target} has no CDP WebSocket endpoint.`);
  // CDP advertises the remote loopback port. Route its path through our tunnel.
  const endpoint = new URL(options.endpoint);
  const socketURL = new URL(page.webSocketDebuggerUrl);
  socketURL.protocol = endpoint.protocol === 'https:' ? 'wss:' : 'ws:';
  socketURL.host = endpoint.host;
  const client = await connect(socketURL, options.timeout);
  try {
    if (options.command === 'inspect') console.log(JSON.stringify(await evaluate(client), null, 2));
    if (options.command === 'screenshot') {
      const { data } = await client.call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
      if (!data) throw new Error('CDP returned no screenshot data.');
      const output = resolve(options.output);
      await writeFile(output, Buffer.from(data, 'base64'));
      console.log(`Saved ${output}`);
    }
    if (options.command === 'click') {
      const { x, y, control } = await evaluate(client, options.label);
      await client.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
      try {
        await client.call('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
        await client.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
        console.log(JSON.stringify({ clicked: control, x, y }, null, 2));
      } catch (error) {
        // A response can time out after the press reached the page. Release it.
        await client.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }).catch(() => {});
        throw error;
      }
    }
  } finally { client.close(); }
}

main().catch((error) => { console.error(`frame-ui: ${error.message}`); process.exitCode = 1; });
