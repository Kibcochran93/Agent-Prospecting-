"""The dashboard's own look and layout, kept out of board_serve.

Design notes, so the next person changing this knows what was deliberate:

Colour carries state and nothing else. All chrome is cool neutral, and the only
saturated things on the page are the four states: done, waiting on a person,
missing, could not be read. A border or a heading is never coloured for effect,
so a splash of colour always means something.

No row of big counters. A dashboard opened five times a day has to answer one
question, "what do I do next", and four numbers make you work out the answer
yourself. The page opens with that answer in words instead.

Tooltips are not where evidence goes. The old grid hid filenames, sequence ids
and dates in title attributes, which cannot be selected, copied, searched or
read on a trackpad. Evidence now lives in an expandable panel per row.

Type is one system family so it renders the same offline, with tabular numerals
so counts do not jitter between refreshes, and mono only for filenames and ids
where the shape of the string is information.
"""

from __future__ import annotations

TOKENS = """
:root {
  --base: #f1f3f4;
  --surface: #ffffff;
  --line: #dfe3e5;
  --line-soft: #eceff0;
  --ink: #16191c;
  --ink-2: #4a545c;
  --ink-3: #79848c;
  --done-bg: #e6f0e9; --done-ink: #1f5c37;
  --wait-bg: #fbf0dc; --wait-ink: #7a5008;
  --miss-bg: #eceff0; --miss-ink: #5a656d;
  --blind-bg: #e8ecf6; --blind-ink: #2f4577; --blind-line: #93a3cc;
  --focus: #1c4f8f;
  --radius: 5px;
  --sans: "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, sans-serif;
  --mono: "Cascadia Mono", "Consolas", ui-monospace, monospace;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 20px 64px; background: var(--base); color: var(--ink);
  font: 15px/1.55 var(--sans); font-variant-numeric: tabular-nums;
}
.page { max-width: 940px; margin: 0 auto; }
a { color: var(--focus); }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }

/* ribbon: one line of standing facts, sticky so it survives scrolling */
.ribbon {
  position: sticky; top: 0; z-index: 5; margin: 0 -20px 26px; padding: 9px 20px;
  background: rgba(241,243,244,.94); border-bottom: 1px solid var(--line);
  backdrop-filter: blur(6px); display: flex; gap: 18px; flex-wrap: wrap;
  align-items: baseline; font-size: 13px; color: var(--ink-2);
}
.ribbon b { color: var(--ink); font-weight: 600; }
.ribbon .alarm { color: var(--wait-ink); }
.ribbon .spacer { flex: 1 1 auto; }

/* the one thing: typographic, not a card */
.now { padding: 30px 0 34px; border-bottom: 1px solid var(--line); }
.now .lede { font-size: 27px; line-height: 1.22; letter-spacing: -.015em;
  font-weight: 600; margin: 0 0 8px; max-width: 30ch; }
.now .sub { margin: 0; color: var(--ink-2); max-width: 68ch; }
.now .cmd { display: flex; gap: 8px; align-items: center; margin-top: 16px; }
.now code { font: 13px/1.5 var(--mono); background: var(--surface);
  border: 1px solid var(--line); border-radius: var(--radius); padding: 7px 10px;
  overflow-x: auto; white-space: nowrap; }

h2 { font-size: 13px; font-weight: 600; color: var(--ink-2); margin: 30px 0 2px; }
h2 .n { color: var(--ink-3); font-weight: 400; }
.hint { margin: 0 0 12px; color: var(--ink-3); font-size: 13px; max-width: 72ch; }

/* rows, not cards: one border, no shadow, hierarchy from spacing */
.rows { background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); overflow: hidden; }
.row { border-top: 1px solid var(--line-soft); }
.row:first-child { border-top: 0; }
.row.on { background: #f7fafc; box-shadow: inset 3px 0 0 var(--focus); }
.row .top { display: flex; gap: 14px; align-items: baseline;
  padding: 11px 14px; cursor: pointer; }
.row .name { font-weight: 600; flex: 0 1 auto; }
.row .said { color: var(--ink-2); font-size: 13px; flex: 1 1 auto; }
.row .act { flex: 0 0 auto; display: flex; gap: 6px; align-items: center; }
.row .why { padding: 0 14px 13px 14px; margin: -4px 0 0; color: var(--ink-2);
  font-size: 13px; max-width: 74ch; }

.pill { display: inline-block; padding: 1px 7px; border-radius: 3px; font-size: 12px;
  white-space: nowrap; }
.done { background: var(--done-bg); color: var(--done-ink); }
.waiting { background: var(--wait-bg); color: var(--wait-ink); }
.missing { background: var(--miss-bg); color: var(--miss-ink); }
.unavailable { background: var(--blind-bg); color: var(--blind-ink);
  border: 1px dashed var(--blind-line); }

button { font: inherit; }
.btn { padding: 4px 11px; border-radius: var(--radius); border: 1px solid var(--line);
  background: var(--surface); color: var(--ink); cursor: pointer; }
.btn:hover { border-color: var(--ink-3); }
.btn.ok.armed { background: var(--done-bg); border-color: var(--done-ink);
  color: var(--done-ink); }
.btn.no.armed { background: #f7e7e7; border-color: #9c4a4a; color: #7d2f2f; }
.btn.run { border-color: #7fae8c; }
.btn.run.armed { background: var(--done-bg); border-color: var(--done-ink); }
.btn.primary { background: var(--ink); color: #fff; border-color: var(--ink);
  padding: 7px 14px; }
.btn[disabled] { opacity: .45; cursor: default; }
.k { font: 11px/1 var(--mono); color: var(--ink-3); border: 1px solid var(--line);
  border-radius: 3px; padding: 2px 4px; margin-left: 6px; }

/* evidence: selectable, not a tooltip */
.ev { display: none; padding: 0 14px 14px; }
.row.open .ev { display: block; }
.ev dl { margin: 0; display: grid; grid-template-columns: 8.5rem 1fr; gap: 4px 12px;
  font-size: 13px; }
.ev dt { color: var(--ink-3); }
.ev dd { margin: 0; }
.ev .file { font: 12px/1.5 var(--mono); color: var(--ink-2); word-break: break-all; }
.ev textarea { width: 100%; margin-top: 10px; font: inherit; padding: 7px 9px;
  border: 1px solid var(--line); border-radius: var(--radius); resize: vertical;
  min-height: 2.4em; background: #fcfdfd; }
.ev label { display: block; font-size: 12px; color: var(--ink-3); margin-top: 10px; }

.filter { display: flex; gap: 8px; align-items: center; margin: 0 0 4px; }
.filter input { flex: 1 1 auto; font: inherit; padding: 7px 10px;
  border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
.empty { padding: 14px; color: var(--ink-3); font-size: 13px; background: var(--surface);
  border: 1px solid var(--line); border-radius: var(--radius); }
.notes { margin: 8px 0 0; padding-left: 18px; color: var(--ink-2); font-size: 13px; }
.notes li { margin-bottom: 4px; }
details.appendix { margin-top: 34px; }
details.appendix summary { cursor: pointer; font-size: 13px; color: var(--ink-3); }
#said { position: fixed; left: 50%; transform: translateX(-50%); bottom: 22px;
  background: var(--ink); color: #fff; padding: 9px 15px; border-radius: var(--radius);
  font-size: 13px; opacity: 0; transition: opacity .14s; pointer-events: none;
  z-index: 20; max-width: 80vw; }
#said.up { opacity: 1; }
#help { display: none; }
#help.up { display: block; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
@media (max-width: 620px) {
  .row .top { flex-wrap: wrap; }
  .ev dl { grid-template-columns: 1fr; }
}
"""


SCRIPT = """
<script>
(function () {
  "use strict";
  var rows = function () {
    return Array.prototype.filter.call(
      document.querySelectorAll('.row'), function (r) { return r.offsetParent !== null; });
  };
  var focused = -1;
  var toast = document.getElementById('said');

  function say(text, ms) {
    toast.textContent = text;
    toast.classList.add('up');
    clearTimeout(say._t);
    say._t = setTimeout(function () { toast.classList.remove('up'); }, ms || 3200);
  }

  function focus(i) {
    var list = rows();
    if (!list.length) return;
    if (i < 0) i = 0;
    if (i >= list.length) i = list.length - 1;
    list.forEach(function (r) { r.classList.remove('on'); });
    focused = i;
    var row = list[i];
    row.classList.add('on');
    row.scrollIntoView({ block: 'nearest' });
  }

  function disarmAll(except) {
    document.querySelectorAll('.btn.armed').forEach(function (b) {
      if (b !== except) { b.classList.remove('armed'); b.textContent = b.dataset.label; }
    });
  }

  // Two clicks, no modal. The first arms the button and shows exactly what will
  // be written; the second writes it. A dialog stops the page and has to be
  // read; an armed button is undoable by looking away.
  function arm(btn) {
    disarmAll(btn);
    btn.dataset.label = btn.dataset.label || btn.textContent;
    btn.classList.add('armed');
    btn.textContent = btn.dataset.confirm || ('Confirm: ' + btn.dataset.label);
    say(btn.dataset.explain || 'Click again to record it.', 6000);
  }

  async function commit(btn) {
    var row = btn.closest('.row');
    var note = row ? row.querySelector('textarea') : null;
    var isRun = btn.classList.contains('run');
    btn.disabled = true;
    say(isRun ? 'Queueing.' : 'Recording.');
    try {
      var resp = await fetch(isRun ? '/queue' : '/decide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json',
                   'Authorization': 'Bearer ' + (window.SEATS_BRIDGE_TOKEN || '') },
        body: JSON.stringify({
          account_key: btn.dataset.key,
          account: btn.dataset.account,
          decision: btn.dataset.decision,
          kind: btn.dataset.kind,
          owner: btn.dataset.owner,
          stage: btn.dataset.stage,
          note: note ? note.value : ''
        })
      });
      var data = await resp.json();
      if (!resp.ok) throw new Error(data.error || 'refused');
      say((isRun ? 'Queued. ' : 'Recorded. ') + data.file, 5000);
      await refresh();
    } catch (err) {
      btn.disabled = false;
      btn.classList.remove('armed');
      btn.textContent = btn.dataset.label;
      say(err.message, 9000);
    }
  }

  // Swap the content, keep the scroll position and the focused row. A full
  // reload throws you back to the top of the page, which is the single most
  // irritating thing about a dashboard you use all day.
  async function refresh() {
    var y = window.scrollY;
    var was = focused;
    var open = Array.prototype.map.call(
      document.querySelectorAll('.row.open'), function (r) { return r.dataset.key; });
    var html = await (await fetch('/', { headers: { 'X-Partial': '1' } })).text();
    var doc = new DOMParser().parseFromString(html, 'text/html');
    document.querySelector('.page').replaceWith(doc.querySelector('.page'));
    open.forEach(function (k) {
      var r = document.querySelector('.row[data-key="' + k + '"]');
      if (r) r.classList.add('open');
    });
    window.scrollTo(0, y);
    if (was >= 0) focus(was);
  }

  document.addEventListener('click', function (event) {
    var btn = event.target.closest('.btn');
    if (btn && btn.dataset.key) {
      event.preventDefault();
      var list = rows();
      var i = list.indexOf(btn.closest('.row'));
      if (i >= 0) focus(i);
      if (btn.classList.contains('armed')) { commit(btn); } else { arm(btn); }
      return;
    }
    if (btn && btn.dataset.copy) {
      navigator.clipboard.writeText(btn.dataset.copy);
      say('Copied.');
      return;
    }
    var top = event.target.closest('.row .top');
    if (top) {
      var row = top.closest('.row');
      row.classList.toggle('open');
      focus(rows().indexOf(row));
    }
  });

  document.addEventListener('keydown', function (event) {
    var tag = (event.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') {
      if (event.key === 'Escape') { event.target.blur(); }
      return;
    }
    var list = rows();
    var row = focused >= 0 ? list[focused] : null;
    var hit = function (sel) { return row ? row.querySelector(sel) : null; };
    switch (event.key) {
      case 'j': case 'ArrowDown': event.preventDefault(); focus(focused + 1); break;
      case 'k': case 'ArrowUp': event.preventDefault(); focus(focused - 1); break;
      case 'Enter': if (row) { row.classList.toggle('open'); } break;
      case 'a': if (hit('.btn.ok')) { var b = hit('.btn.ok');
                  b.classList.contains('armed') ? commit(b) : arm(b); } break;
      case 'd': if (hit('.btn.no')) { var n = hit('.btn.no');
                  n.classList.contains('armed') ? commit(n) : arm(n); } break;
      case 'r': if (hit('.btn.run')) { var q = hit('.btn.run');
                  q.classList.contains('armed') ? commit(q) : arm(q); } break;
      case 'n': if (row) { row.classList.add('open');
                  var t = row.querySelector('textarea'); if (t) { t.focus(); } } break;
      case '/': event.preventDefault();
                var f = document.getElementById('filter'); if (f) { f.focus(); } break;
      case '?': document.getElementById('help').classList.toggle('up'); break;
      case 'Escape': disarmAll(null); break;
      default: return;
    }
  });

  var filter = document.getElementById('filter');
  if (filter) {
    filter.addEventListener('input', function () {
      var q = filter.value.trim().toLowerCase();
      document.querySelectorAll('.row').forEach(function (r) {
        var name = (r.dataset.name || '').toLowerCase();
        r.style.display = (!q || name.indexOf(q) >= 0) ? '' : 'none';
      });
      focused = -1;
    });
  }
})();
</script>
"""


def esc(text) -> str:
    import html as _h

    return _h.escape(str(text or ""), quote=True)


def button(kind, label, key, account, *, decision="", stage="", owner="",
           confirm="", explain="", extra="") -> str:
    """One action. Two clicks, and the second one is the one that writes."""
    return (
        f'<button class="btn {kind}" data-key="{esc(key)}" data-account="{esc(account)}" '
        f'data-decision="{esc(decision)}" data-stage="{esc(stage)}" '
        f'data-owner="{esc(owner)}" data-kind="{esc(extra)}" '
        f'data-label="{esc(label)}" data-confirm="{esc(confirm)}" '
        f'data-explain="{esc(explain)}">{esc(label)}</button>'
    )


def row(key, name, said, why, actions, evidence) -> str:
    ev = "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in evidence)
    return (
        f'<div class="row" data-key="{esc(key)}" data-name="{esc(name)}">'
        f'<div class="top"><span class="name">{esc(name)}</span>'
        f'<span class="said">{said}</span>'
        f'<span class="act">{actions}</span></div>'
        f'<p class="why">{esc(why)}</p>'
        f'<div class="ev"><dl>{ev}</dl>'
        '<label>Note, saved with whatever you record next. Optional. '
        '<span class="k">n</span></label>'
        '<textarea rows="2"></textarea></div></div>'
    )


def rows_or_empty(items, empty_text) -> str:
    if not items:
        return f'<p class="empty">{esc(empty_text)}</p>'
    return '<div class="rows">' + "".join(items) + "</div>"


HELP = """
<div id="help" class="empty"><strong>Keys.</strong>
<span class="k">j</span> <span class="k">k</span> move &middot;
<span class="k">Enter</span> show evidence &middot;
<span class="k">a</span> approve &middot;
<span class="k">d</span> decline &middot;
<span class="k">r</span> queue the run &middot;
<span class="k">n</span> write a note &middot;
<span class="k">/</span> filter &middot;
<span class="k">Esc</span> cancel &middot;
<span class="k">?</span> this.
Every action needs two presses: the first arms it and says what it will write,
the second writes it.</div>
"""


def page(*, generated, accounts, steps, standing, owners, resolutions, jobs_mod,
         all_jobs, budget_left, waiting, defects, unavailable, disagreements,
         buckets, appendix, bridge_token: str = "") -> str:
    """Assemble the page. The Now block answers the question; lists follow."""
    from seats_prospecting import next_step as ns

    inbound = buckets.get("inbound", [])
    yours = buckets["yours"]
    ready = buckets["ready"]
    ask = buckets["ask"]
    cleared = buckets["cleared"]
    running = buckets["running"]
    resting = buckets["resting"]

    runner_cmd = r".venv\Scripts\python.exe scripts\run_queue.py --loop"

    # The Now block. One answer, chosen in the order things actually block on.
    if waiting and budget_left >= 0 and not any(
        j.started_at[:10] == generated.date().isoformat() for j in all_jobs
    ):
        lede = f"{waiting} job{'s' if waiting != 1 else ''} queued. Nothing is running them."
        sub = ("The runner is a separate program, on purpose, so a run has a log and "
               "a browser tab cannot start one by accident. Start it in a terminal "
               "and it will take them oldest first.")
        cmd = ('<div class="cmd"><code>' + esc(runner_cmd) + "</code>"
               + f'<button class="btn" data-copy="{esc(runner_cmd)}">Copy</button></div>')
    elif inbound:
        first = inbound[0][1]
        who = first.intent.person if first.intent else "someone"
        lede = f"{who} at {first.display} has been reading the site."
        sub = (f"{len(inbound)} institution{'s' if len(inbound) != 1 else ''} came "
               "to us and nobody has researched them. Inbound beats anything in "
               "the queue: these people arrived on their own.")
        cmd = ""
    elif yours:
        lede = f"{len(yours)} account{'s' if len(yours) != 1 else ''} need you in Apollo."
        sub = "Nothing here can send. These are waiting on you to do it."
        cmd = ""
    elif ask:
        first = ask[0][1]
        lede = f"Ask {first.owner or 'the account owner'} about {first.display}."
        sub = (f"{len(ask)} colleague-owned account{'s' if len(ask) != 1 else ''} "
               "with no answer on record. They have no login here, so nothing "
               "resolves this but a conversation. Record what they say.")
        cmd = ""
    elif ready and budget_left > 0:
        lede = f"{len(ready)} account{'s' if len(ready) != 1 else ''} ready to run."
        sub = f"Queue one and the runner picks it up. {budget_left} runs left today."
        cmd = ""
    elif ready:
        lede = "Out of run budget for today."
        sub = (f"{len(ready)} accounts are ready and the ceiling is "
               f"{jobs_mod.MAX_PER_DAY} runs a day. They keep until tomorrow.")
        cmd = ""
    else:
        lede = "Nothing needs you."
        sub = "No decisions pending, nothing queued, nothing overdue that Apollo will admit to."
        cmd = ""

    out = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>SE Prospecting</title>",
        f"<style>{TOKENS}</style></head><body><div class=\"page\">",
        '<div class="ribbon">',
        f"<span>{esc(generated.strftime('%a %d %b, %H:%M'))} UTC</span>",
        f"<span><b>{len(accounts)}</b> accounts</span>",
        f"<span><b>{budget_left}</b> of {jobs_mod.MAX_PER_DAY} runs left today</span>",
        (f'<span class="alarm"><b>{waiting}</b> queued, runner not started</span>'
         if waiting else "<span>queue empty</span>"),
        '<span class="spacer"></span>',
        '<span>nothing here can send</span>',
        "</div>",
        f'<section class="now"><p class="lede">{esc(lede)}</p>'
        f'<p class="sub">{esc(sub)}</p>{cmd}</section>',
        HELP,
    ]

    def ev_common(acct, step, key):
        res = resolutions.get(key)
        items = [("Owner", esc(acct.owner or "not recorded")),
                 ("Stage", esc(step.stage) + ", waiting on " + esc(step.waiting_on))]
        if res and res.record_said:
            items.append(("Record says", esc(res.record_said)))
        if res and res.owner:
            items.append(("Owner review", esc(res.owner.decision) + " by "
                          + esc(res.owner.decided_by)
                          + (", recorded by " + esc(res.owner.recorded_by)
                             if res.owner.secondhand else "")))
        if res and res.kib:
            items.append(("Your decision", esc(res.kib.decision) + " on "
                          + esc(res.kib.decided_at[:10])))
        if acct.verified_through:
            items.append(("Oldest source", esc(acct.verified_through)))
        files = [e["file"] for e in acct.briefing + acct.context][:4]
        if files:
            items.append(("Records", '<span class="file">'
                          + esc("; ".join(files)) + "</span>"))
        if acct.sequences:
            items.append(("Apollo", '<span class="file">' + esc("; ".join(
                f"{s['name']} ({s['id']})" for s in acct.sequences)) + "</span>"))
        if step.evidence:
            items.append(("Checked", esc(step.evidence)))
        return items

    if yours:
        out.append(f'<h2>Your move <span class="n">{len(yours)}</span></h2>')
        out.append('<p class="hint">Nobody else can do these.</p>')
        out.append(rows_or_empty([
            row(k, a.display, f'<span class="pill waiting">{esc(s.stage)}</span>',
                s.blocker, "", ev_common(a, s, k)) for k, a, s, _h in yours], ""))

    if inbound:
        out.append(f'<h2>They came to us <span class="n">{len(inbound)}</span></h2>')
        out.append('<p class="hint">Named people reading seatsone.com, with no '
                   "research on record. Internal only: never quote this to them and "
                   "never imply their browsing was watched.</p>")
        rws = []
        for k, a, s, _held in inbound:
            btn = button("run", "Run INBOUND", k, a.display, stage=s.stage,
                         confirm="Confirm: research this account",
                         explain=f"Queues a context check for {a.display}.")
            i = a.intent
            said = (f'<span class="pill waiting">{i.total_visits} visits</span> '
                    f'<span class="muted">last {esc(i.last_visit)}</span>') if i else ""
            ev = [("Person", esc(i.person) + ", " + esc(i.person_title)),
                  ("Visits", f"{i.total_visits}, most recent {esc(i.last_visit)}"),
                  ("Apollo intent", esc(i.apollo_intent or "not computed")),
                  ("Source", esc(i.source)),
                  ("Age", f"{i.age_days} days" + (", stale" if i.stale else "")),
                  ("Use", "Targeting and research only. Not usable in copy."),
                  ("Record", '<span class="file">' + esc(i.file) + "</span>")] if i else []
            rws.append(row(k, a.display, said, s.blocker, btn, ev))
        out.append(rows_or_empty(rws, ""))

    out.append(f'<h2>Ready to run <span class="n">{len(ready)}</span></h2>')
    out.append('<p class="hint">Approved, and the next step is one a tool can take. '
               "A click queues it; the runner does the work.</p>")
    ready_rows = []
    for k, a, s, held in ready:
        if budget_left <= 0:
            act = ('<button class="btn" disabled>no budget today</button>')
        else:
            act = button("run", f"Run {s.stage}", k, a.display, stage=s.stage,
                         confirm=f"Confirm: run {s.stage}",
                         explain=f"Queues a {s.stage} job for {a.display}. "
                                 "It spends credits when the runner reaches it.")
        said = (f'<span class="pill done">approved {esc(held.decided_at[:10])}</span>'
                if held else "")
        ready_rows.append(row(k, a.display, said, s.blocker, act, ev_common(a, s, k)))
    out.append(rows_or_empty(ready_rows, "Nothing is queueable right now."))

    out.append(f'<h2>Owners to ask <span class="n">{len(ask)}</span></h2>')
    out.append('<p class="hint">Recording an answer names them as the decider and '
               "you as the person who wrote it down.</p>")
    ask_rows = []
    for k, a, s, _held in ask:
        who = a.owner or "the owner"
        act = (button("ok", f"{who} said yes", k, a.display, decision="Approved",
                      owner=a.owner, extra="owner_review",
                      confirm=f"Confirm: {who} said yes",
                      explain=f"Records {who} as having cleared {a.display}, "
                              "with you as the person who wrote it down.")
               + button("no", f"{who} said no", k, a.display, decision="Declined",
                        owner=a.owner, extra="owner_review",
                        confirm=f"Confirm: {who} said no",
                        explain=f"Records {who} as having declined {a.display}."))
        ask_rows.append(row(k, a.display, "", s.blocker, act, ev_common(a, s, k)))
    out.append(rows_or_empty(ask_rows, "Nobody to chase."))

    if cleared:
        out.append(f'<h2>Cleared by the owner, waiting on you '
                   f'<span class="n">{len(cleared)}</span></h2>')
        rws = []
        for k, a, s, _held in cleared:
            act = (button("ok", "Approve", k, a.display, decision="Approved",
                          extra="kib_decision", confirm="Confirm: approve",
                          explain=f"Approves outreach for {a.display}.")
                   + button("no", "Decline", k, a.display, decision="Declined",
                            extra="kib_decision", confirm="Confirm: decline",
                            explain=f"Declines outreach for {a.display}."))
            rws.append(row(k, a.display, "", s.blocker, act, ev_common(a, s, k)))
        out.append(rows_or_empty(rws, ""))

    if running:
        out.append(f'<h2>Queued and run today <span class="n">{len(running)}</span></h2>')
        rws = []
        for k, a, s, job in running:
            state = {"done": "done", "failed": "missing",
                     "refused": "missing"}.get(job.state, "waiting")
            said = f'<span class="pill {state}">{esc(job.stage)} {esc(job.state)}</span>'
            ev = ev_common(a, s, k) + [
                ("Job", '<span class="file">' + esc(job.id) + "</span>"),
                ("Log", '<span class="file">' + esc(job.log or "none yet") + "</span>"),
                ("Outcome", esc(job.outcome or "not run yet")),
            ]
            rws.append(row(k, a.display, said, job.outcome or
                           "Queued. Waiting for the runner.", "", ev))
        out.append(rows_or_empty(rws, ""))

    if disagreements:
        out.append(f'<h2>Your decision overrode the record '
                   f'<span class="n">{len(disagreements)}</span></h2>')
        out.append('<p class="hint">Your call governs. This is what it governs over.</p>'
                   '<ul class="notes">')
        for name, why in disagreements:
            out.append(f"<li><strong>{esc(name)}</strong>: {esc(why)}</li>")
        out.append("</ul>")

    blind = defects + unavailable
    out.append(f'<h2>Faults and blind spots <span class="n">{len(blind)}</span></h2>')
    out.append('<p class="hint">Left visible. A blind spot shown as a zero is worse '
               "than one shown as a gap.</p>")
    if blind:
        out.append('<ul class="notes">')
        out += [f"<li>{esc(item)}</li>" for item in blind]
        out.append("</ul>")
    else:
        out.append('<p class="empty">None.</p>')

    if resting:
        out.append(f'<h2>Nothing needed <span class="n">{len(resting)}</span></h2>'
                   '<ul class="notes">')
        for k, a, s, _j in resting:
            out.append(f"<li>{esc(a.display)}: {esc(s.blocker[:120])}</li>")
        out.append("</ul>")

    out.append('<div class="filter"><input id="filter" type="search" '
               'placeholder="Filter by account  /" aria-label="Filter by account"></div>')
    out.append('<details class="appendix"><summary>Full grid, one row per account, '
               "and what this page read</summary>" + appendix + "</details>")
    out.append('<div id="said" role="status" aria-live="polite"></div>')
    import json as _json
    token_script = "<script>window.SEATS_BRIDGE_TOKEN = " + _json.dumps(bridge_token) + ";</script>"
    out.append("</div>" + token_script + SCRIPT + "</body></html>")
    return "".join(out)
