# BBLOT dashboard

Static site for "Boys Boys Lots of Toys", a 12-team Sleeper dynasty league (league id 1330268610945376256), rebuilt by GitHub
Actions and served on GitHub Pages: https://mpenney94-blip.github.io/bblot/

It started as a port of the Clampions dashboard (repo `mpenney94-blip/clampions`); the code layout and conventions are the
same, with the dynasty changes below. Fixes to shared code may be worth carrying across by hand, but the repos don't share code.

## Layout

- `sleeper.py`: Sleeper API client. The players DB is cached in `.cache/` for a day, written atomically because season builds run in parallel.
- `winprob.py`: ESPN scoreboard and play-by-play helpers, projections, and the play-by-play win probability model.
- `dashboard/config.py`: league constants: `LEAGUE_ID`, `FIRST_SEASON` (2025), `league_chain()` (seasons back through
  `previous_league_id`, stopping before 2025), FAAB % formatting (`pct_text`, `money_text`), low/high score cutoffs.
- `dashboard/stats.py`: `League` (one season's data) plus season stats and `season_summary()` (feeds the all-time page).
- `dashboard/weekly.py`: playoff odds and clinches, power, awards, preview, trends, byes/injuries, career days, flagged plays, kicking.
- `dashboard/dynasty.py`: roster value with age, contention window, future pick ownership, taxi/IR tracker, dynasty power
  (current season only), and the all-time draft hit rates, startup redraft and pick trade ledger.
- `dashboard/nemesis.py`: Nemeses (the NFL players who scored the most against each team from opponents' lineups), season
  and all-time (from `nemeses` in each summary.json). The same file is in the Clampions and BBLOT repos.
- `dashboard/commentary.py`: fact sheets and the Claude-written columns (recap, preview, rivalry), with a built-in fallback writer.
- `dashboard/league_notes.md`: league lore fed to the column writer. Fresh for this league (nothing from Clampions); the
  owner will send lore to add. Every manager is he/him (`config.SHE` is empty).
- `dashboard/render.py`: all HTML/CSS/JS. `dashboard/build.py`: assembles pages, season tabs and the all-time page.
- `.github/workflows/dashboard.yml`: builds and deploys.

## Building and checking

```
python3 -m dashboard.build 1330268610945376256 --out <dir> --no-winprob --past-cache <dir>   # all seasons
python3 -m dashboard.build 1330268610945376256 --out <dir> --no-winprob --only 2026            # current season only
```

Set `COMMENTARY_CACHE=<dir>` for local builds so they don't touch `.cache/commentary`. Check pages with Playwright
(`PW=$(npm root -g)/playwright node script.js`): screenshot the section, click its controls, and listen for `pageerror`.
Run the build without piping its exit code away; an exception mid-build leaves an empty page.

## Table conventions

- Every sortable table gets both a "Sort by" menu and clickable column headers, kept in sync by the shared sorter in
  `HIGHLIGHT_JS`. Use either `_sort_controls()` inside a `.sortbars` panel or `<table class="sortable">`. Give cells a
  `data-v` when their text isn't the sort value.
- Expandable rows (`tr.xrow` + `tr.xdetail`) stay attached when sorted; any `table.expand` with 2+ dropdown rows gets an
  "Expand all / Collapse all" button automatically.
- Grid/heatmap tables use `.grid-t`, with headers centered over their cells.
- When a section is added or meaningfully changed, add its id and the date to `SECTION_NEW` in `dashboard/build.py`: the
  top menu tags it "new" for 14 days.
- Every `section[id] > h2` gets a "Save image" button (html2canvas). html2canvas can't parse `color-mix()`; use plain
  colors or the `--t1`..`--t6` tints.

## Workflow conventions

- Batch changes: one branch and one PR for a run of requests, merged with `expectedHeadSha`, then confirm the deploy by
  polling the live site or the Actions run gently (once a minute or so). In November 2026 GitHub briefly suspended the
  account for bot-like activity across these repos.
- Commit trailers: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and the `Claude-Session:` link.
- Pushing to `main` under `dashboard/**`, `sleeper.py`, `winprob.py` or the workflow triggers a build. Code changes
  invalidate the finished-season cache.
- Scheduled runs: Tuesday 4am and Thursday noon Pacific (both UTC times listed; a `gate` job lets the right one through).

## League rules (encoded; playoff format to be confirmed by the owner)

- Dynasty from 2025 (the startup draft: 26-round snake). Seasons before 2025 were redraft and are ignored. Every later
  draft is a 5-round linear annual expansion draft: rookies plus any player not on a roster (not "rookie draft": veterans can
  be taken). Draft sections are titled by kind ("Startup draft" / "Expansion draft"). The expansion draft's redraft only
  ranks players who were in that pool (`stats.pre_draft_rostered` rebuilds the rosters at draft time).
- 12 teams, 2 divisions. Playoffs (6 teams): each division winner gets a bye, then the next 4 best records regardless of
  division; points for breaks ties (`weekly` seeding and `_misses`).
- FAAB: $100 in 2025, $1,000 from 2026 (`lg.faab_budget` from `waiver_budget`). Every FAAB number is shown as a % of
  that season's budget, with dollars alongside where useful.
- Roster: 3 taxi and 2 IR spots. Taxi and IR players are kept out of lineup math (best lineup, bench points, perfect
  lineups, start/sit): `stats._inactive_players` saves a snapshot of each team's taxi + IR lists for the coming week in
  `.cache/commentary/inactive-<league>.json` on every build of a current season; weeks without a snapshot fall back to
  today's taxi lists (taxi players can only be promoted). Their points sit in `m["inactive_points"]`.
- Scoring all comes from Sleeper's settings (kicker scoring verified: 190 games, 0 mismatches).
- No keepers in this league; the keeper sections were removed.
- "Drafted vs. picked up" (`stats.sources`, all-time too) credits starting-lineup points by how the manager got the player,
  across every dynasty season (`stats.acquisition_log` reads each earlier season's drafts and transactions): startup draft,
  each expansion draft by year, waiver/FA pickup, or trade. Each team has a dropdown of the players behind each share.
- The expansion draft's redraft ranks pool players by points scored; the startup redraft keeps points above replacement.

## Dynasty model (`dashboard/dynasty.py`)

- Points per game = this season plus last season at half weight. Edge = points per game over replacement (QB 14th, RB
  30th, WR 42nd, TE 14th at his position). Value = edge over 5 seasons, each later one at 85%, losing 30% a year past
  the position cliff (QB 35, RB 27, WR 29, TE 30), +15% a year for first- and second-year players. A first-year player taken in this
  year's expansion draft is worth at least his pick. Pick value by round = the value of the league's 30th/70th/110th/150th/190th player.
- Contention tags from all-play rank (thirds) and this season's trades: win-now edge from players 26+ gained or given
  away; young players and picks count as building. Tags: Contender, All in, Winning while selling, Pushing, Retooling,
  In the middle, Failed tank (sold but top half), Failed push (bought, bottom third), Rebuilding, Stuck.
- `commentary.dynasty_facts` passes the tags (`dynasty_situation`) to the latest recap and the coming preview only;
  the SYSTEM prompt tells the writer to judge teams by their plan and call out failed tanks and failed pushes.
- All-time trades and drops are judged across seasons (`season_pts` and `draft_slots` in each summary.json); traded
  picks are valued by the player drafted with them.

## Written columns (Claude)

- Model `claude-opus-5-5` (effort medium) with server-side fallbacks, `ANTHROPIC_API_KEY` from the repo secret. Current
  season only; finished seasons use the built-in writer. Cached in `.cache/commentary` (Actions cache) as {paras, facts}.
- A recap, or a preview of a week already played, is written once and kept; the live preview is rewritten on scheduled
  and manual runs; the rivalry column about every 55 days on a Tuesday run.

## To do / open with the owner

- League lore for `league_notes.md` (coming from the owner).
- Confirm the playoff format and consolation bracket details.
- Taxi/IR snapshots only exist from when this site started (week 12 of 2026); earlier weeks use today's taxi lists.
