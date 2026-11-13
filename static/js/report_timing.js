/* Minutes-per-report clock, one per player report.
 *
 * The clock starts when that player's report is opened, not when the batch
 * was sent for analysis: in a five-player batch the fifth report would
 * otherwise be charged for the time spent reading the first four.
 *
 * The time is frozen on the first Reviewed or Export click. If saving fails,
 * the report is not marked reviewed and the same frozen time is sent again on
 * retry, so a slow retry never inflates the measurement.
 *
 * No DOM access here, so it runs under `node --test` (tests/js/).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.FVTiming = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function start (report, analyzeStartedAt, readyAt) {
    report.timing = { analyzeStartedAt: analyzeStartedAt, readyAt: readyAt, openedAt: null,
                      frozen: null, state: 'idle', error: '', needsKey: false };
  }

  // First open starts the clock. Closing and reopening does not restart it.
  function open (report, now) {
    var t = report.timing;
    if (t && t.openedAt == null) t.openedAt = now;
  }

  function reason (status) {
    if (status === 401) return 'a scout key is needed';
    if (status === 503) return 'this server has no scout key set up';
    return 'the server answered ' + status;
  }

  // post(numbers) -> Promise<{ok, status}>. Resolves to the new state.
  async function finish (report, how, now, post) {
    var t = report.timing;
    if (!t || t.openedAt == null) return 'not-open';
    if (t.state === 'saved' || t.state === 'saving') return t.state;
    if (!t.frozen) t.frozen = { how: how, seconds: Math.max((now - t.openedAt) / 1000, 0.1) };
    t.state = 'saving';
    t.error = '';
    var error = '';
    try {
      var res = await post({
        // Model wait for the Analyze run this report came from. A batch shares it.
        seconds_to_report: Math.max((t.readyAt - t.analyzeStartedAt) / 1000, 0.1),
        // This player's report opened -> reviewed or exported.
        seconds_to_finish: t.frozen.seconds,
        finished_by: t.frozen.how,
      });
      if (!res || !res.ok) {
        error = reason(res ? res.status : 0);
        t.needsKey = !!res && res.status === 401;
      }
    } catch (e) {
      error = 'the server could not be reached';
    }
    t.state = error ? 'failed' : 'saved';
    t.error = error;
    return t.state;
  }

  function reviewMinutes (report) {
    var t = report.timing;
    return t && t.frozen ? t.frozen.seconds / 60 : null;
  }

  return { start: start, open: open, finish: finish, reviewMinutes: reviewMinutes };
});
