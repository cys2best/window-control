const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const modulePath = path.join(__dirname, '../../src/client/input_channel.js');

function createFrames() {
  let nextId = 1;
  const queued = new Map();
  return {
    schedule(callback) {
      const id = nextId++;
      queued.set(id, callback);
      return id;
    },
    cancel(id) {
      queued.delete(id);
    },
    flushOne() {
      const entry = queued.entries().next().value;
      if (!entry) return false;
      const [id, callback] = entry;
      queued.delete(id);
      callback();
      return true;
    },
    get size() {
      return queued.size;
    },
  };
}

function createChannel() {
  return {
    readyState: 'open',
    bufferedAmount: 0,
    sent: [],
    send(payload) {
      this.sent.push(payload);
    },
    json() {
      return this.sent.map((payload) => JSON.parse(payload));
    },
  };
}

function loadInputModule() {
  const frames = createFrames();
  const channel = createChannel();
  const context = { globalThis: {}, Object };
  vm.runInNewContext(fs.readFileSync(modulePath, 'utf8'), context, { filename: modulePath });
  return { api: context.globalThis.WindowControlInput, channel, frames };
}

test('coalesces drag moves and flushes the last coordinate before end', () => {
  const { api, channel, frames } = loadInputModule();
  const sender = api.createSender(channel, { schedule: frames.schedule, cancel: frames.cancel, highWaterMark: 16 });

  sender.dragStart(0.1, 0.2);
  sender.dragMove(0.3, 0.4);
  sender.dragMove(0.8, 0.9);
  frames.flushOne();
  sender.dragEnd(0.9, 1.0);

  assert.deepEqual(channel.json(), [
    { type: 'drag_start', x: 0.1, y: 0.2 },
    { type: 'drag_move', x: 0.8, y: 0.9 },
    { type: 'drag_move', x: 0.9, y: 1.0 },
    { type: 'drag_end', x: 0.9, y: 1.0 },
  ]);
});

test('accumulates scroll while buffered and preserves current direction', () => {
  const { api, channel, frames } = loadInputModule();
  const sender = api.createSender(channel, { schedule: frames.schedule, cancel: frames.cancel, highWaterMark: 16 });
  channel.bufferedAmount = 32;
  sender.scroll(0.5, 0.5, -0.04);
  sender.scroll(0.5, 0.5, -0.06);
  frames.flushOne();
  assert.equal(channel.sent.length, 0);
  channel.bufferedAmount = 0;
  frames.flushOne();
  assert.deepEqual(channel.json()[0], { type: 'scroll', x: 0.5, y: 0.5, dy: -0.10 });
});

test('sends discrete key, IDR, and echo messages immediately only when open', () => {
  const { api, channel } = loadInputModule();
  const sender = api.createSender(channel);
  sender.send({ type: 'key', key: 'Return' });
  sender.send({ type: 'idr' });
  sender.send({ type: 'echo', ts: 12 });
  channel.readyState = 'closed';
  sender.send({ type: 'key', key: 'Escape' });
  assert.deepEqual(channel.json(), [
    { type: 'key', key: 'Return' }, { type: 'idr' }, { type: 'echo', ts: 12 },
  ]);
});

test('sends a tap as immediate drag start and end without click', () => {
  const { api, channel } = loadInputModule();
  const sender = api.createSender(channel);
  sender.dragStart(0.2, 0.3);
  sender.dragEnd(0.2, 0.3);
  assert.deepEqual(channel.json(), [
    { type: 'drag_start', x: 0.2, y: 0.3 }, { type: 'drag_end', x: 0.2, y: 0.3 },
  ]);
});

test('clamps accumulated normalized scroll to one bounded delta', () => {
  const { api, channel, frames } = loadInputModule();
  const sender = api.createSender(channel, { schedule: frames.schedule, cancel: frames.cancel });
  sender.scroll(0.1, 0.2, 0.2);
  sender.scroll(0.1, 0.2, 0.2);
  frames.flushOne();
  assert.deepEqual(channel.json(), [{ type: 'scroll', x: 0.1, y: 0.2, dy: 0.25 }]);
});

test('keeps pending scroll scheduled when drag end cancels a move frame', () => {
  const { api, channel, frames } = loadInputModule();
  const sender = api.createSender(channel, { schedule: frames.schedule, cancel: frames.cancel });
  sender.dragStart(0.1, 0.2);
  sender.dragMove(0.3, 0.4);
  sender.scroll(0.5, 0.6, -0.1);
  sender.dragEnd(0.3, 0.4);
  frames.flushOne();
  assert.deepEqual(channel.json(), [
    { type: 'drag_start', x: 0.1, y: 0.2 },
    { type: 'drag_move', x: 0.3, y: 0.4 },
    { type: 'drag_end', x: 0.3, y: 0.4 },
    { type: 'scroll', x: 0.5, y: 0.6, dy: -0.1 },
  ]);
});

test('close cancels scheduled motion and prevents post-close frames', () => {
  const { api, channel, frames } = loadInputModule();
  const sender = api.createSender(channel, { schedule: frames.schedule, cancel: frames.cancel });
  sender.dragMove(0.4, 0.5);
  sender.close();
  assert.equal(frames.size, 0);
  assert.equal(frames.flushOne(), false);
  assert.deepEqual(channel.json(), []);
});

test('drops queued motion when the channel closes', () => {
  const { api, channel, frames } = loadInputModule();
  const sender = api.createSender(channel, { schedule: frames.schedule, cancel: frames.cancel });
  sender.dragMove(0.4, 0.5);
  channel.readyState = 'closed';
  frames.flushOne();
  assert.equal(frames.size, 0);
  assert.deepEqual(channel.json(), []);
});

test('a fresh sender does not inherit pending motion from a closed sender', () => {
  const { api, channel, frames } = loadInputModule();
  const first = api.createSender(channel, { schedule: frames.schedule, cancel: frames.cancel });
  first.dragMove(0.4, 0.5);
  first.close();
  const second = api.createSender(channel, { schedule: frames.schedule, cancel: frames.cancel });
  second.scroll(0.6, 0.7, 0.1);
  frames.flushOne();
  assert.deepEqual(channel.json(), [{ type: 'scroll', x: 0.6, y: 0.7, dy: 0.1 }]);
});
