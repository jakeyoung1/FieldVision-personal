// Run with: node --test tests/js/
const test = require('node:test');
const assert = require('node:assert/strict');
const T = require('../../static/js/report_timing.js');

const ok = async () => ({ ok: true, status: 200 });
const fail = status => async () => ({ ok: false, status });

function batch (n, analyzeStartedAt, readyAt) {
  return Array.from({ length: n }, () => { const r = {}; T.start(r, analyzeStartedAt, readyAt); return r; });
}

test('each player\'s clock starts when that report is opened, not at Analyze', async () => {
  const [a, b] = batch(2, 0, 30_000);            // Analyze at 0s, both ready at 30s
  T.open(a, 40_000);                             // first report opened at 40s
  T.open(b, 400_000);                            // second opened at 400s, after reading the first
  let sent;
  await T.finish(a, 'reviewed', 100_000, async body => { sent = body; return { ok: true, status: 200 }; });
  assert.equal(sent.seconds_to_finish, 60);      // 40s -> 100s
  assert.equal(sent.seconds_to_report, 30);      // model wait, shared by the batch
  await T.finish(b, 'reviewed', 460_000, async body => { sent = body; return { ok: true, status: 200 }; });
  assert.equal(sent.seconds_to_finish, 60);      // 400s -> 460s, not 460s from Analyze
});

test('a report that was never opened cannot be finished', async () => {
  const [r] = batch(1, 0, 1000);
  let posted = false;
  assert.equal(await T.finish(r, 'reviewed', 5000, async () => { posted = true; return { ok: true }; }), 'not-open');
  assert.equal(posted, false);
});

test('reopening a report does not restart its clock', () => {
  const [r] = batch(1, 0, 1000);
  T.open(r, 2000);
  T.open(r, 90_000);
  assert.equal(r.timing.openedAt, 2000);
});

for (const [name, post, text] of [
  ['401', fail(401), 'a scout key is needed'],
  ['503', fail(503), 'this server has no scout key set up'],
  ['500', fail(500), 'the server answered 500'],
  ['network error', async () => { throw new Error('offline'); }, 'the server could not be reached'],
]) {
  test(`a failed save (${name}) reports an error and does not mark the report reviewed`, async () => {
    const [r] = batch(1, 0, 1000);
    T.open(r, 1000);
    assert.equal(await T.finish(r, 'reviewed', 61_000, post), 'failed');
    assert.equal(r.timing.state, 'failed');
    assert.notEqual(r.timing.state, 'saved');
    assert.equal(r.timing.error, text);
  });
}

test('a failed save can be retried, and the retry sends the time frozen at the first click', async () => {
  const [r] = batch(1, 0, 1000);
  T.open(r, 1000);
  await T.finish(r, 'reviewed', 61_000, fail(401));
  assert.equal(r.timing.needsKey, true);
  let sent;
  const state = await T.finish(r, 'reviewed', 999_000, async body => { sent = body; return { ok: true, status: 200 }; });
  assert.equal(state, 'saved');
  assert.equal(sent.seconds_to_finish, 60);      // not 998s: the retry was slow, the review was not
  assert.equal(T.reviewMinutes(r), 1);
});

test('a saved report is not logged twice', async () => {
  const [r] = batch(1, 0, 1000);
  T.open(r, 1000);
  let calls = 0;
  const post = async () => { calls += 1; return { ok: true, status: 200 }; };
  await T.finish(r, 'reviewed', 5000, post);
  await T.finish(r, 'exported', 9000, post);
  assert.equal(calls, 1);
});

test('a second click while a save is in flight does not send twice', async () => {
  const [r] = batch(1, 0, 1000);
  T.open(r, 1000);
  let release, calls = 0;
  const slow = () => { calls += 1; return new Promise(res => { release = () => res({ ok: true, status: 200 }); }); };
  const first = T.finish(r, 'reviewed', 5000, slow);
  assert.equal(await T.finish(r, 'reviewed', 6000, slow), 'saving');
  release();
  assert.equal(await first, 'saved');
  assert.equal(calls, 1);
});
