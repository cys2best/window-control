(function (global) {
  'use strict';

  function sessionError(code, message) {
    const error = new Error(message);
    error.code = code;
    return error;
  }

  function waitForIceGatheringComplete(pc) {
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise(function (resolve) {
      function complete() {
        if (pc.iceGatheringState === 'complete') resolve();
      }
      pc.addEventListener('icegatheringstatechange', complete);
      complete();
    });
  }

  function createManager(options) {
    const deps = options || {};
    const fetchImpl = deps.fetchImpl || global.fetch;
    const PeerConnection = deps.PeerConnection || global.RTCPeerConnection;
    const WebSocketImpl = deps.WebSocketImpl || global.WebSocket;
    const inputApi = deps.inputApi || global.WindowControlInput;
    const timeoutMs = deps.timeoutMs === undefined ? 8000 : deps.timeoutMs;
    const disconnectedGraceMs = deps.disconnectedGraceMs === undefined ? 6000 : deps.disconnectedGraceMs;
    let active = null;
    let pending = null;
    let managerClosed = false;

    function createAttempt(kind, selection, callbacks) {
      const pc = new PeerConnection({ iceServers: selection.ice_servers || [] });
      let attempt;
      try {
      let channel = null;
      let input;
      let resourceUrl = null;
      let resourceDeleted = false;
      let ws = null;
      let closed = false;
      let adopted = false;
      let iceReady = pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed';
      let videoStream = null;
      let channelReady = false;
      let readyResolve;
      let readyReject;
      let timeout = null;
      let disconnectedTimer = null;
      const ready = new Promise(function (resolve, reject) { readyResolve = resolve; readyReject = reject; });

      attempt = {
        kind: kind,
        pc: pc,
        get channel() { return channel; },
        get ws() { return ws; },
        get closed() { return closed; },
        setWebSocket: function (socket) { ws = socket; },
        setResourceUrl: function (url) { resourceUrl = url; },
        markAdopted: function () { adopted = true; },
        ready: ready,
        fail: fail,
        abort: function (error) {
          if (closed) return Promise.resolve();
          readyReject(error || sessionError('closed', 'Engine session closed'));
          return close();
        },
        close: close,
        session: null,
      };

      function deleteResource() {
        if (kind !== 'local' || !resourceUrl || resourceDeleted) return Promise.resolve();
        resourceDeleted = true;
        return Promise.resolve(fetchImpl(resourceUrl, { method: 'DELETE' })).catch(function () {});
      }

      function close() {
        if (closed) return deleteResource();
        closed = true;
        if (timeout !== null) global.clearTimeout(timeout);
        if (disconnectedTimer !== null) global.clearTimeout(disconnectedTimer);
        if (input && typeof input.close === 'function') input.close();
        if (channel && typeof channel.close === 'function') channel.close();
        if (ws && typeof ws.close === 'function') ws.close();
        if (pc && typeof pc.close === 'function') pc.close();
        return deleteResource();
      }

      function fail(error) {
        if (closed) return;
        const failure = error instanceof Error ? error : sessionError('failed', String(error));
        if (adopted) {
          if (callbacks.onState) callbacks.onState('failed');
          close();
          return;
        }
        readyReject(failure);
        close();
      }

      function checkReady() {
        if (closed || !iceReady || !videoStream || !channelReady) return;
        if (timeout !== null) global.clearTimeout(timeout);
        readyResolve(attempt);
      }

      function listen(target, type, listener) {
        if (target && typeof target.addEventListener === 'function') target.addEventListener(type, listener);
        else if (target) target[`on${type}`] = listener;
      }

      listen(pc, 'track', function (event) {
        if (closed) return;
        if (!event.track || event.track.kind !== 'video') return;
        videoStream = event.streams && event.streams[0];
        if (!videoStream) return;
        if (callbacks.onTrack) callbacks.onTrack(videoStream);
        checkReady();
      });
      listen(pc, 'iceconnectionstatechange', function () {
        if (closed) return;
        const state = pc.iceConnectionState;
        if (state === 'connected' || state === 'completed') {
          if (disconnectedTimer !== null) {
            global.clearTimeout(disconnectedTimer);
            disconnectedTimer = null;
          }
          iceReady = true;
          checkReady();
        } else if (state === 'failed' || state === 'closed') {
          if (disconnectedTimer !== null) {
            global.clearTimeout(disconnectedTimer);
            disconnectedTimer = null;
          }
          fail(sessionError('ice-failed', `ICE connection ${state}`));
        } else if (state === 'disconnected') {
          // Spec/implementation-dependent: a dead remote peer often sits in
          // 'disconnected' rather than transitioning to 'failed' on its own
          // (e.g. the engine process was killed, so nothing ever signals
          // ICE failure). Give it a grace period to self-heal (a brief
          // network blip) before treating it the same as 'failed'.
          if (disconnectedTimer === null) {
            disconnectedTimer = global.setTimeout(function () {
              disconnectedTimer = null;
              if (closed || pc.iceConnectionState !== 'disconnected') return;
              fail(sessionError('ice-failed', 'ICE connection disconnected'));
            }, disconnectedGraceMs);
          }
        }
      });

      pc.addTransceiver('video', { direction: 'recvonly' });
      channel = pc.createDataChannel('input', { ordered: true });
      input = inputApi.createSender(channel);
      channelReady = channel.readyState === 'open';
      listen(channel, 'open', function () {
        if (closed) return;
        channelReady = true;
        checkReady();
      });
      listen(channel, 'message', function (event) {
        if (!closed && callbacks.onInputMessage) callbacks.onInputMessage(event.data, event);
      });
      listen(channel, 'close', function () { fail(sessionError('input-closed', 'Input channel closed')); });
      listen(channel, 'error', function () { fail(sessionError('input-failed', 'Input channel failed')); });
      if (timeoutMs > 0) timeout = global.setTimeout(function () { fail(sessionError('timeout', 'Engine session timed out')); }, timeoutMs);

      attempt.session = {
        kind: kind,
        pc: pc,
        input: input,
        get stream() { return videoStream; },
        close: function () { return close(); },
      };
      return attempt;
      } catch (error) {
        if (attempt) attempt.close();
        else if (pc && typeof pc.close === 'function') pc.close();
        throw error;
      }
    }

    function failedAttempt(kind, error) {
      return {
        kind: kind,
        ready: Promise.reject(error),
        fail: function () {},
        abort: function () { return Promise.resolve(); },
        close: function () { return Promise.resolve(); },
      };
    }

    async function negotiate(attempt) {
      const offer = await attempt.pc.createOffer();
      if (attempt.closed) throw sessionError('closed', 'Engine session closed');
      await attempt.pc.setLocalDescription(offer);
      await waitForIceGatheringComplete(attempt.pc);
      if (attempt.closed) throw sessionError('closed', 'Engine session closed');
    }

    function startLocal(selection, callbacks) {
      let attempt;
      try {
        attempt = createAttempt('local', selection, callbacks);
      } catch (error) {
        return failedAttempt('local', error);
      }
      (async function () {
        try {
          await negotiate(attempt);
          const response = await fetchImpl(selection.whep_url, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/sdp',
              'Authorization': `Bearer ${selection.whep_token}`,
            },
            body: attempt.pc.localDescription.sdp,
          });
          if (!response.ok) {
            const code = response.status === 401 ? 'credential-expired' : response.status === 503 ? 'capacity' : 'whep-failed';
            throw sessionError(code, `WHEP POST failed (${response.status})`);
          }
          const location = response.headers && response.headers.get('Location');
          if (!location) throw sessionError('missing-location', 'WHEP response is missing Location');
          attempt.setResourceUrl(new URL(location, selection.whep_url).href);
          if (attempt.closed) {
            await attempt.close();
            throw sessionError('closed', 'Engine session closed');
          }
          await attempt.pc.setRemoteDescription({ type: 'answer', sdp: await response.text() });
        } catch (error) {
          attempt.fail(error);
        }
      })();
      return attempt;
    }

    function startPublic(selection, callbacks) {
      let attempt;
      try {
        attempt = createAttempt('public', selection, callbacks);
      } catch (error) {
        return failedAttempt('public', error);
      }
      let answerApplied = false;
      let closingSignaling = false;
      let sessionReady = false;
      (async function () {
        try {
          await negotiate(attempt);
          if (attempt.closed) return;
          const url = new URL(selection.signaling_url);
          url.searchParams.set('session', selection.name);
          url.searchParams.set('role', 'viewer');
          url.searchParams.set('token', selection.signaling_token);
          const ws = new WebSocketImpl(url.href);
          attempt.setWebSocket(ws);
          function listen(type, listener) {
            if (typeof ws.addEventListener === 'function') ws.addEventListener(type, listener);
            else ws[`on${type}`] = listener;
          }
          listen('open', function () {
            if (!attempt.closed) ws.send(attempt.pc.localDescription.sdp);
          });
          listen('message', async function (event) {
            if (attempt.closed || answerApplied) return;
            try {
              await attempt.pc.setRemoteDescription({ type: 'answer', sdp: event.data });
              answerApplied = true;
            } catch (error) {
              attempt.fail(error);
            }
          });
          listen('error', function () { attempt.fail(sessionError('signaling-failed', 'Public signaling failed')); });
          listen('close', function () {
            if (!attempt.closed && !closingSignaling && !sessionReady) {
              attempt.fail(sessionError('signaling-closed', 'Public signaling closed before readiness'));
            }
          });
          await attempt.ready;
          if (!attempt.closed) {
            sessionReady = true;
            closingSignaling = true;
            ws.close();
          }
        } catch (error) {
          attempt.fail(error);
        }
      })();
      return attempt;
    }

    function closeAttempts(attempts) {
      return Promise.all(attempts.map(function (attempt) {
        return attempt.close();
      }));
    }

    function connect(selection, callbacks) {
      callbacks = callbacks || {};
      if (managerClosed) return Promise.reject(sessionError('closed', 'Engine session manager is closed'));
      if (pending) pending.cancel(sessionError('superseded', 'Engine session connect superseded'));
      const localConfigured = !!(selection.whep_url && selection.whep_token);
      const publicConfigured = !!(selection.signaling_url && selection.signaling_token);
      if (!localConfigured && !publicConfigured) return Promise.reject(sessionError('unconfigured', 'No engine session transport is configured'));
      const attempts = [];
      if (localConfigured) attempts.push(startLocal(selection, callbacks));
      if (publicConfigured) attempts.push(startPublic(selection, callbacks));
      if (callbacks.onState) callbacks.onState('connecting');
      let cancelled = false;
      let cancelError = null;
      const group = {
        cancel: function (error) {
          if (cancelled) return;
          cancelled = true;
          cancelError = error;
          attempts.forEach(function (attempt) { attempt.abort(error); });
        },
      };
      pending = group;

      return new Promise(function (resolve, reject) {
        let failures = 0;
        attempts.forEach(function (attempt) {
          attempt.ready.then(async function (winner) {
            if (cancelled || pending !== group || managerClosed) {
              await winner.close();
              reject(cancelError || sessionError('closed', 'Engine session manager is closed'));
              return;
            }
            winner.markAdopted();
            await closeAttempts(attempts.filter(function (other) { return other !== winner; }));
            if (cancelled || pending !== group || managerClosed) {
              await winner.close();
              reject(cancelError || sessionError('closed', 'Engine session manager is closed'));
              return;
            }
            // Reserve the ready replacement now, but leave predecessor cleanup
            // to the UI after it has attached the replacement stream. A WHEP
            // DELETE can wait on the server, so awaiting it here creates a
            // visible gap despite the replacement already being ready.
            active = winner.session;
            pending = null;
            if (callbacks.onState) callbacks.onState('connected');
            resolve(winner.session);
          }, function (error) {
            failures += 1;
            if (cancelled || pending !== group) {
              if (failures === attempts.length) reject(cancelError || error);
              return;
            }
            if (failures === attempts.length) {
              pending = null;
              reject(attempts.length === 1 ? error : sessionError('all-failed', 'All engine session attempts failed'));
            }
          });
        });
      });
    }

    return {
      connect: connect,
      close: async function () {
        managerClosed = true;
        if (pending) pending.cancel(sessionError('closed', 'Engine session manager is closed'));
        pending = null;
        if (active) {
          const previous = active;
          active = null;
          await previous.close();
        }
      },
    };
  }

  global.WindowControlEngineSessions = Object.freeze({ createManager: createManager });
})(globalThis);
