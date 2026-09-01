(function (global) {
  'use strict';

  const MAX_SCROLL_DELTA = 0.25;

  function createSender(channel, options) {
    options = options || {};
    const schedule = options.schedule || global.requestAnimationFrame;
    const cancel = options.cancel || global.cancelAnimationFrame;
    const highWaterMark = options.highWaterMark === undefined ? 16 * 1024 : options.highWaterMark;
    let pendingMove = null;
    let pendingScroll = null;
    let frame = null;
    let dragMoved = false;
    let closed = false;
    let generation = 0;

    function isOpen() {
      return !closed && channel && channel.readyState === 'open';
    }

    function send(message) {
      if (!isOpen()) return false;
      channel.send(JSON.stringify(message));
      return true;
    }

    function hasMotion() {
      return pendingMove !== null || pendingScroll !== null;
    }

    function scheduleFlush() {
      if (closed || frame !== null || !hasMotion()) return;
      const scheduledGeneration = generation;
      frame = schedule(function () {
        frame = null;
        if (closed || scheduledGeneration !== generation) return;
        flush();
      });
    }

    function flush() {
      if (!hasMotion()) return;
      if (!isOpen()) {
        pendingMove = null;
        pendingScroll = null;
        return;
      }
      if (channel.bufferedAmount > highWaterMark) {
        scheduleFlush();
        return;
      }
      if (pendingMove !== null) {
        send({ type: 'drag_move', x: pendingMove.x, y: pendingMove.y });
        pendingMove = null;
      }
      if (pendingScroll !== null) {
        send({ type: 'scroll', x: pendingScroll.x, y: pendingScroll.y, dy: pendingScroll.dy });
        pendingScroll = null;
      }
    }

    function wakeMotion() {
      if (!closed && hasMotion() && channel.bufferedAmount <= highWaterMark) scheduleFlush();
    }

    if (channel && typeof channel.addEventListener === 'function') {
      channel.addEventListener('bufferedamountlow', wakeMotion);
    }

    return {
      send: send,
      dragStart: function (x, y) {
        if (closed) return;
        dragMoved = false;
        send({ type: 'drag_start', x: x, y: y });
      },
      dragMove: function (x, y) {
        if (closed) return;
        dragMoved = true;
        pendingMove = { x: x, y: y };
        scheduleFlush();
      },
      dragEnd: function (x, y) {
        if (closed) return;
        if (frame !== null) {
          cancel(frame);
          frame = null;
        }
        if (dragMoved) {
          pendingMove = null;
          send({ type: 'drag_move', x: x, y: y });
        }
        send({ type: 'drag_end', x: x, y: y });
        dragMoved = false;
        pendingMove = null;
        scheduleFlush();
      },
      scroll: function (x, y, dy) {
        if (closed) return;
        const accumulated = (pendingScroll ? pendingScroll.dy : 0) + dy;
        pendingScroll = {
          x: x,
          y: y,
          dy: Math.max(-MAX_SCROLL_DELTA, Math.min(MAX_SCROLL_DELTA, accumulated)),
        };
        scheduleFlush();
      },
      close: function () {
        if (closed) return;
        closed = true;
        generation += 1;
        if (frame !== null) cancel(frame);
        frame = null;
        pendingMove = null;
        pendingScroll = null;
        dragMoved = false;
      },
    };
  }

  global.WindowControlInput = Object.freeze({ createSender: createSender });
})(globalThis);
