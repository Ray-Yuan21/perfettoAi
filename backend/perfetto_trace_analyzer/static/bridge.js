(function() {
  // Unregister any cached service workers
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.getRegistrations().then(function(regs) {
      regs.forEach(function(r) { r.unregister(); });
    });
  }

  // Track last jump note so we can replace it on each jump
  var _lastJumpNoteId = null;

  function getTrace() {
    var app = window.app;
    if (!app) return null;
    if (app._activeTrace) return app._activeTrace;
    if (app.trace && typeof app.trace !== 'function') return app.trace;
    if (typeof app.trace === 'function') {
      try { var t = app.trace(); if (t) return t; } catch(e) {}
    }
    return null;
  }

  window._perfettoJump = async function(ts, dur, processName, upid, jankCategory, sliceType) {
    var app = window.app;
    if (!app) { console.log('[Bridge] no app'); return; }
    var trace = getTrace();
    if (!trace || !trace.timeline) { console.log('[Bridge] no trace/timeline'); return; }

    var tl = trace.timeline;
    var viewDur = Math.max(dur * 10, 50000000);
    var viewStart = Math.max(0, ts - viewDur / 2);

    // 1. Set visible window (viewport zoom)
    try {
      var vw = tl._visibleWindow;
      var StartClass = vw.start.constructor;
      var WindowClass = vw.constructor;
      var newStart = new StartClass(BigInt(viewStart));
      var newWindow = new WindowClass(newStart, Number(viewDur));
      tl.setVisibleWindow(newWindow);
    } catch(e) {
      console.log('[Bridge] setVisibleWindow error:', e);
    }

    // 2. Create persistent span note (M-key style)
    try {
      var notes = trace.notes;
      if (_lastJumpNoteId !== null) {
        try { notes.removeNote(_lastJumpNoteId); } catch(e) {}
      }
      _lastJumpNoteId = notes.addSpanNote({
        start: BigInt(ts),
        end: BigInt(ts) + BigInt(dur),
        color: '#3b82f6'
      });
    } catch(e) {
      console.log('[Bridge] note error:', e);
    }

    // 3. Select the slice + scroll to it
    var selected = false;
    var verticalScrolled = false;
    sliceType = sliceType || 'frame';

    // Step A: Select the slice via SQL
    try {
      if (trace.engine) {
        var jankCat = jankCategory || '';
        var isSF = jankCat && (jankCat.indexOf('SF') >= 0 || jankCat.indexOf('sf') >= 0);
        var sql;
        var tableName;

        if (sliceType === 'slice') {
          // Call tree slice: search in regular slice table by ts and dur
          // Use range matching to handle potential precision issues with large integers
          tableName = 'slice';
          var tsTolerance = 1000; // 1 microsecond tolerance
          sql = "SELECT CAST(id AS TEXT) AS id_text, ts, dur, track_id FROM slice WHERE ABS(ts - " + ts + ") <= " + tsTolerance + " AND ABS(dur - " + dur + ") <= " + tsTolerance;
          sql += " ORDER BY ABS(ts - " + ts + ") + ABS(dur - " + dur + ") LIMIT 1";
          console.log('[Bridge] searching slice table for call tree item, ts=' + ts + ' dur=' + dur);
        } else if (isSF) {
          // SF frames: use time range overlap matching (ts may not match exactly)
          tableName = 'actual_frame_timeline_slice';
          sql = "SELECT CAST(id AS TEXT) AS id_text FROM actual_frame_timeline_slice WHERE ts <= " + ts + " AND (ts + dur) >= " + ts;
          if (upid) sql += " AND upid = " + upid;
          sql += " ORDER BY ABS(ts - " + ts + ") LIMIT 1";
        } else {
          // App frames: exact ts match
          tableName = 'actual_frame_timeline_slice';
          sql = "SELECT CAST(id AS TEXT) AS id_text FROM actual_frame_timeline_slice WHERE ts = " + ts;
          if (upid) sql += " AND upid = " + upid;
          sql += " LIMIT 1";
        }

        console.log('[Bridge] SQL:', sql);
        var result = await trace.engine.query(sql);
        var it = result.iter({id_text: 'str'});

        if (it.valid()) {
          var sliceId = parseInt(it.id_text, 10);
          var foundTs = it.ts !== undefined ? it.ts : ts;
          var foundDur = it.dur !== undefined ? it.dur : dur;
          var trackId = undefined;
          if (sliceType === 'slice') {
            try {
              trackId = it.get('track_id');
            } catch (e) {
              console.log('[Bridge] could not get track_id:', e.message);
            }
          }
          console.log('[Bridge] found slice id:', sliceId, 'in table:', tableName, 'track_id:', trackId);

          if (trace.selection && typeof trace.selection.selectSqlEvent === 'function') {
            trace.selection.selectSqlEvent(tableName, sliceId, {scrollToSelection: true});
            selected = true;
            console.log('[Bridge] selectSqlEvent OK');

            // For slice type, try to scroll to the specific track
            if (sliceType === 'slice' && trackId && trace.scrollHelper) {
              setTimeout(function() {
                try {
                  var targetUri = '/slice_' + trackId;
                  var ws = trace.workspace || (trace.workspaces && (trace.workspaces.currentWorkspace || trace.workspaces.defaultWorkspace));
                  if (ws && ws.tracks) {
                    var found = false;
                    function findS(node) {
                      if (found || !node) return;
                      // Match Exact URI or trackIds array
                      if ((node.uri && node.uri.indexOf(trackId) >= 0) || (node.trackIds && node.trackIds.includes(trackId))) {
                        targetUri = node.uri;
                        found = true;
                        return;
                      }
                      var children = node.children || node.flatTracks;
                      if (children) {
                        for (var i = 0; i < children.length; i++) findS(children[i]);
                      }
                    }
                    for (var i = 0; i < ws.tracks.length; i++) findS(ws.tracks[i]);
                  }
                  console.log('[Bridge] resolved track scroll uri:', targetUri);
                  trace.scrollHelper.scrollTo({track: {uri: targetUri, expandGroup: true}});
                } catch(e) {
                  console.log('[Bridge] track scroll error:', e);
                }
              }, 50);
            }
          }
        } else {
          console.log('[Bridge] no slice found in', tableName, 'ts=' + ts + ' dur=' + dur);
        }
      }
    } catch(e) {
      console.log('[Bridge] select error:', e);
    }

    // Step B: Expand and scroll to the process group
    var trace2 = getTrace() || trace;

    if (upid) {
      var processUri = '/process_' + upid;

      // Expand the process group
      try {
        var ws = trace2.workspace || (trace2.workspaces && (trace2.workspaces.currentWorkspace || trace2.workspaces.defaultWorkspace));
        var processNode = null;
        if (ws && ws.tracks) {
          function findP(node) {
            if (processNode || !node) return;
            // Match the UPID anywhere in the URI
            if (node.uri && node.uri.indexOf('process_' + upid) >= 0) {
              processNode = node;
              return;
            }
            var children = node.children || node.flatTracks;
            if (children) {
              for (var i = 0; i < children.length; i++) findP(children[i]);
            }
          }
          for (var i = 0; i < ws.tracks.length; i++) {
            findP(ws.tracks[i]);
            if (processNode) break;
          }
        }
        
        if (!processNode && ws && typeof ws.getTrackByUri === 'function') {
          processNode = ws.getTrackByUri(processUri);
        }

        if (processNode) {
          processUri = processNode.uri; // Use the exact resolved URI
          // Expand the process group
          if ('expanded' in processNode) processNode.expanded = true;
          if ('collapsed' in processNode) processNode.collapsed = false;
          if (typeof processNode.expand === 'function') processNode.expand();
          if (typeof processNode.toggle === 'function' && !processNode.expanded) processNode.toggle();

          // Expand parents too
          var p = processNode.parent;
          while (p) {
            if ('expanded' in p) p.expanded = true;
            if ('collapsed' in p) p.collapsed = false;
            if (typeof p.expand === 'function') p.expand();
            p = p.parent;
          }
        }
      } catch(e) {
        console.log('[Bridge] expand error:', e);
      }

      // Redraw first, then scroll after UI updates
      app.raf.scheduleFullRedraw();
      setTimeout(function() {
        if (trace2.scrollHelper) {
          try {
            trace2.scrollHelper.scrollTo({track: {uri: processUri, expandGroup: true}});
          } catch(e) {
            console.log('[Bridge] process scroll error:', e);
          }
        }
        app.raf.scheduleFullRedraw();
      }, 100);

      verticalScrolled = true;
    }

    if (!verticalScrolled) {
      console.log('[Bridge] vertical scroll failed. upid:', upid, 'process:', processName);
    }

    // 5. Force redraw
    app.raf.scheduleFullRedraw();
    try { app.raf.syncCanvasRedraw(); } catch(e) {}
    setTimeout(function() {
      app.raf.scheduleFullRedraw();
      try { app.raf.syncCanvasRedraw(); } catch(e) {}
    }, 100);
  };

  // ── WebSocket: receive jump commands from MCP / external tools ──
  function connectBridgeWS() {
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = proto + '//' + location.host + '/ws/bridge';
    var ws = new WebSocket(url);
    ws.onopen = function() { console.log('[Bridge] WS connected'); };
    ws.onmessage = function(evt) {
      try {
        var msg = JSON.parse(evt.data);
        if (msg.type === 'jump' && msg.ts !== undefined) {
          console.log('[Bridge] WS jump ts=' + msg.ts + ' dur=' + msg.dur + ' slice_type=' + (msg.slice_type || 'frame'));
          window._perfettoJump(msg.ts, msg.dur || 0, msg.process_name || '', msg.upid || 0, msg.jank_category || '', msg.slice_type || 'frame');
        }
      } catch(e) { console.log('[Bridge] WS message error:', e); }
    };
    ws.onclose = function() {
      console.log('[Bridge] WS disconnected, reconnecting in 3s');
      setTimeout(connectBridgeWS, 3000);
    };
    ws.onerror = function() { ws.close(); };
  }
  // Wait for page load before connecting WS
  if (document.readyState === 'complete') { connectBridgeWS(); }
  else { window.addEventListener('load', connectBridgeWS); }
})();
