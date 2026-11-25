"""Dynasty views for the current season: future pick ownership, an age-aware roster value, each team's contention window,
the taxi squad and IR, and dynasty power rankings. All of it describes rosters as they stand today, so it's only built for
a season still in progress."""
import collections

from dashboard import stats
from dashboard.render import esc, signed

# Age a position usually starts to fall off (the last age of a full-value season), and how fast it falls after.
CLIFF = {"QB": 35, "RB": 27, "WR": 29, "TE": 30, "K": 37}
DROP = 0.3  # share of value lost each year past the cliff
YEARS = 5  # seasons counted in dynasty value, this one included
DISCOUNT = 0.85  # each later season counts this much less than the one before
GROWTH = 0.15  # yearly gain for players in their first two NFL seasons, for the next two seasons
# Replacement level: the player at this rank at his position (about what a 12-team league starts, flex included).
REPL_RANK = {"QB": 14, "RB": 30, "WR": 42, "TE": 14, "K": 13, "DEF": 13}
# A rookie pick is worth what the player at this league-wide dynasty value rank is worth, by round.
PICK_RANK = {1: 30, 2: 70, 3: 110, 4: 150, 5: 190}


def _games(lg, pid):
    ws = [w for w in lg.weeks if w in lg.reg_weeks and pid in lg.played[w]]
    return sum(lg.pts[pid].get(w, 0) for w in ws), len(ws)


def value_table(lg):
    """{pid: {...}} for every player on a roster today: points per game (this season, with last season at half weight),
    points per game above replacement at his position, and dynasty value (that edge over the next YEARS seasons with
    age, growth and discount applied)."""
    last = (lg.past_sums[0].get("season_pts") or {}) if getattr(lg, "past_sums", None) else {}
    out = {}
    for r, pl in lg.roster_now.items():
        for pid in pl:
            info = lg.players.get(pid) or {}
            p_now, g_now = _games(lg, pid)
            p_last, g_last = last.get(pid, [0, 0])
            g = g_now + 0.5 * g_last
            ppg = (p_now + 0.5 * p_last) / g if g else 0.0
            out[pid] = {"pid": pid, "roster": r, "name": lg.name_of(pid), "pos": lg.pos_of(pid), "age": info.get("age"),
                        "exp": info.get("years_exp") or 0, "ppg": ppg, "games": g_now, "nfl": info.get("team") or "FA"}
    by_pos = collections.defaultdict(list)
    for v in out.values():
        if v["games"] + (1 if v["ppg"] else 0) >= 2:  # one big game alone doesn't set the bar
            by_pos[v["pos"]].append(v["ppg"])
    repl = {p: (sorted(xs, reverse=True)[REPL_RANK.get(p, 13) - 1] if len(xs) >= REPL_RANK.get(p, 13) else min(xs)) for p, xs in by_pos.items()}
    for v in out.values():
        edge = max(0.0, v["ppg"] - repl.get(v["pos"], 0))
        v["edge"] = edge
        v["by_year"] = [edge * _age_factor(v, t) for t in range(YEARS)]
        v["value"] = sum(x * DISCOUNT ** t for t, x in enumerate(v["by_year"]))
    # rookies drafted this year who haven't shown much yet keep at least the value of the pick they cost
    ranked = sorted((v["value"] for v in out.values()), reverse=True)
    pv = pick_values(ranked)
    if int(lg.season) > 2025:
        for d in stats.draft_slots(lg):
            v = out.get(d["pid"])
            if v and v["exp"] == 0 and v["value"] < pv[d["round"]]:
                v["value"], v["floor"] = pv[d["round"]], d["round"]
    return out, pv


def _age_factor(v, t):
    if v["pos"] == "DEF":
        return 1.0 if t < 2 else 0.0  # a team defense is a year-to-year pickup
    age = v["age"]
    if age is None:
        return 1.0 if t == 0 else 0.5
    f = (1 + GROWTH) ** min(t, max(0, 2 - v["exp"]))
    past = age + t - CLIFF.get(v["pos"], 30)
    return max(0.0, f * (1 - DROP * past if past > 0 else 1))


def pick_values(ranked):
    return {rd: (ranked[k - 1] if len(ranked) >= k else 0) for rd, k in PICK_RANK.items()}


def pick_ownership(lg):
    """{(season, round, original roster): owning roster} for the next three rookie drafts, from Sleeper's traded picks."""
    rounds = lg.info["settings"].get("draft_rounds") or 5
    first = int(lg.season) + 1
    own = {(str(s), rd, r): r for s in range(first, first + 3) for rd in range(1, rounds + 1) for r in lg.team}
    for t in lg.client.traded_picks(lg.id):
        k = (str(t["season"]), t["round"], t["roster_id"])
        if k in own:
            own[k] = t["owner_id"]
    return own


def pick_total(own, pv, roster, season0):
    return sum(pv.get(rd, 0) * DISCOUNT ** (int(s) - season0) for (s, rd, _), o in own.items() if o == roster)


def trade_lean(lg, vals):
    """roster -> {"now": points-per-game edge gained (+) or given away (-) in this season's trades, "picks": pick
    value gained or given away, "moves": [text]} — who's buying for now and who's selling for later."""
    pv = pick_values(sorted((v["value"] for v in vals.values()), reverse=True))
    out = collections.defaultdict(lambda: {"now": 0.0, "picks": 0.0, "future": 0.0, "moves": []})
    for t in stats.trades(lg):
        for sd in t["sides"]:
            r = next(r for r, n in lg.team.items() if n == sd["team"])
            others = [o for o in t["sides"] if o is not sd]
            for g in sd["got"]:
                v = vals.get(g["pid"])
                e = _edge_now(lg, g["pid"], vals)
                out[r]["now"] += e
                out[r]["future"] += (v["value"] if v else 0) - e
                for o in others:
                    ro = next(rr for rr, n in lg.team.items() if n == o["team"])
                    out[ro]["now"] -= e
                    out[ro]["future"] -= (v["value"] if v else 0) - e
            for pk in sd["pick_list"]:
                if int(pk["season"]) > int(lg.season):
                    out[r]["picks"] += pv.get(pk["round"], 0)
                    for o in others:
                        ro = next(rr for rr, n in lg.team.items() if n == o["team"])
                        out[ro]["picks"] -= pv.get(pk["round"], 0)
            got = [f'{g["player"]}' for g in sd["got"]] + [f'{pk["season"]} R{pk["round"]}' for pk in sd["pick_list"]]
            if got:
                out[r]["moves"].append(f'wk {t["weeks"][0] if t["weeks"] else lg.weeks[-1]}: got ' + ", ".join(got))
    return out


def _edge_now(lg, pid, vals):
    """Points per game over replacement that count as win-now help: veterans (26 and up) and team defenses. A young
    player's production goes with his future value instead, so trading for him reads as building, not buying."""
    v = vals.get(pid)
    if not v or (v["age"] is not None and v["age"] < 26):
        return 0.0
    return v["edge"]


def contention(lg, vals, pv, own):
    """One row per team: how good it is now, how its roster ages, its picks, what its trades say, and a verdict."""
    ap = {x["team"]: x for x in stats.allplay(lg)}
    lean = trade_lean(lg, vals)
    season0 = int(lg.season) + 1
    rows = []
    for r, name in lg.team.items():
        mine = sorted((v for v in vals.values() if v["roster"] == r), key=lambda v: -v["edge"])
        core = [v for v in mine[:10] if v["age"] is not None]
        wsum = sum(v["edge"] for v in core) or 1
        x = ap[name]
        rows.append({"team": name, "roster": r, "ap": x["pct"], "rec": x["record"],
                     "core_age": sum(v["age"] * v["edge"] for v in core) / wsum if core else 0,
                     "now": sum(v["by_year"][0] for v in mine), "next": sum(v["by_year"][1] for v in mine), "later": sum(v["by_year"][2] for v in mine),
                     "value": sum(v["value"] for v in mine), "picks": pick_total(own, pv, r, season0),
                     "firsts": sum(1 for (s, rd, _), o in own.items() if o == r and rd == 1 and int(s) == season0),
                     "firsts_all": sum(1 for (s, rd, _), o in own.items() if o == r and rd == 1),
                     "lean": lean[r] if r in lean else {"now": 0.0, "picks": 0.0, "future": 0.0, "moves": []}})
    by_ap = sorted(rows, key=lambda x: -x["ap"])
    for i, x in enumerate(by_ap, 1):
        x["now_rank"] = i
    for i, x in enumerate(sorted(rows, key=lambda x: -(x["value"] + x["picks"])), 1):
        x["future_rank"] = i
    n = len(rows)
    for x in rows:
        x["pick1"] = pv.get(1, 10)
        x["tag"], x["why"] = _verdict(x, n)
    return sorted(rows, key=lambda x: x["now_rank"])


def _verdict(x, n):
    """Contender / middle / rebuilder from today's all-play rank, then whether this season's trades match it."""
    now = x["now_rank"]
    L = x["lean"]
    buy = L["now"] >= 2.0
    sell = L["now"] <= -2.0 or (L["now"] <= 0 and L["picks"] + L["future"] >= x.get("pick1", 10) / 2)
    young = x["core_age"] and x["core_age"] < 25.5
    if now <= n // 3:
        if sell:
            return "Winning while selling", "top-third team that has traded points away this season"
        return ("All in", "top-third team that traded for help") if buy else ("Contender", "top-third team by all-play")
    if now > n - n // 3:
        if buy:
            return "Failed push", "traded for points now and still in the bottom third"
        if sell or young:
            return "Rebuilding", "bottom-third team selling for picks or built on young players"
        return "Stuck", "bottom third with no clear plan in the trade market yet"
    if sell and now <= n // 2:
        return "Failed tank", "sold points for the future but keeps winning, which costs draft position"
    if buy:
        return "Pushing", "middle of the pack and buying"
    if sell:
        return "Retooling", "middle of the pack and selling"
    return "In the middle", "middle third by all-play, quiet in trades"


def taxi_rows(lg, vals):
    """Today's taxi squads and IR, with what each player has scored this season while stashed."""
    stashed_w, stashed_pts, would = collections.Counter(), collections.Counter(), collections.Counter()
    for w in lg.weeks:
        for m in lg.matchups[w]:
            r = m["roster_id"]
            for p, v in (m.get("inactive_points") or {}).items():
                stashed_w[(r, p)] += 1
                stashed_pts[(r, p)] += v or 0
                # would he have outscored the weakest starter he could have replaced?
                pos = lg.pos_of(p)
                low = [sv or 0 for s, sp, sv in zip(lg.slots, m["starters"], m.get("starters_points") or [])
                       if pos in stats.FLEX.get(s, (s,))]
                if low and (v or 0) > min(low):
                    would[(r, p)] += 1
    out = []
    for r, name in lg.team.items():
        def row(p, where):
            v = vals.get(p) or {}
            return {"pid": p, "name": lg.name_of(p), "pos": lg.pos_of(p), "age": v.get("age"), "exp": v.get("exp", 0), "where": where,
                    "weeks": stashed_w[(r, p)], "pts": stashed_pts[(r, p)], "would": would[(r, p)], "value": v.get("value", 0),
                    "nfl": v.get("nfl", "")}
        taxi = [row(p, "Taxi") for p in sorted(lg.taxi_now.get(r, ()))]
        ir = [row(p, "IR") for p in sorted(lg.reserve_now.get(r, ()))]
        out.append({"team": name, "players": taxi + ir, "taxi": len(taxi), "ir": len(ir),
                    "stuck": sum(x["pts"] for x in taxi + ir), "would": sum(x["would"] for x in taxi + ir),
                    "value": sum(x["value"] for x in taxi)})
    return sorted(out, key=lambda t: -t["value"])


def power(rows):
    """Dynasty power: 45% this season (all-play), 40% roster value, 15% future picks, each as a share of the league best."""
    top = lambda k: max(x[k] for x in rows) or 1
    a, v, p = top("ap"), top("value"), top("picks")
    for x in rows:
        x["power"] = 100 * (0.45 * x["ap"] / a + 0.40 * x["value"] / v + 0.15 * x["picks"] / p)
    return sorted(rows, key=lambda x: -x["power"])


def tags_for_columns(lg):
    """{team: {tag, why, all_play_rank, core_age, firsts_next_draft, trades}} for the recap and preview facts."""
    vals, pv = value_table(lg)
    rows = contention(lg, vals, pv, pick_ownership(lg))
    return {x["team"]: {"dynasty_tag": x["tag"], "why": x["why"], "all_play_rank": x["now_rank"], "core_age": round(x["core_age"], 1),
                        "dynasty_value_rank": x["future_rank"], "first_round_picks_next_draft": x["firsts"],
                        "this_seasons_trades": x["lean"]["moves"][-4:]} for x in rows}


# ---------------------------------------------------------------- HTML
def _bar(v, top, cls="pos"):
    w = 0 if not top else max(0, min(100, 100 * v / top))
    bg = "var(--a)" if cls == "pos" else "var(--mark)"
    return (f'<div class="track" style="display:inline-block;width:110px;vertical-align:middle">'
            f'<span style="width:{w:.0f}%;background:{bg}"></span></div>')


def _team(n):
    return f'<span class="team">{esc(n)}</span>'


TAG_CLASS = {"All in": "pos", "Contender": "pos", "Winning while selling": "pos", "Rebuilding": "muted", "Failed push": "neg",
             "Failed tank": "neg", "Stuck": "neg"}


def sections(lg):
    """[(id, title, html)] for the dynasty group of the current season's page."""
    vals, pv = value_table(lg)
    own = pick_ownership(lg)
    rows = contention(lg, vals, pv, own)
    s0 = int(lg.season) + 1
    avg_v = sum(x["value"] for x in rows) / len(rows) or 1
    out = []

    # dynasty power
    pw = power([dict(x) for x in rows])
    body = "".join(
        f'<tr><td class="muted rank">{i}</td><td>{_team(x["team"])}<div class="muted" style="font-size:12px">'
        f'<span class="{TAG_CLASS.get(x["tag"], "")}">{esc(x["tag"])}</span></div></td>'
        f'<td class="n" data-v="{x["power"]:.2f}"><b>{x["power"]:.0f}</b></td>'
        f'<td class="n" data-v="{x["ap"]:.4f}">{100 * x["ap"]:.0f}% <span class="muted">#{x["now_rank"]}</span></td>'
        f'<td class="n" data-v="{x["value"]:.2f}">{100 * x["value"] / avg_v:.0f}</td>'
        f'<td class="n" data-v="{x["picks"]:.2f}">{x["firsts_all"]} <span class="muted">1sts</span></td></tr>'
        for i, x in enumerate(pw, 1))
    out.append(("dynasty-power", "Dynasty power rankings",
                '<p class="lede">Who is built to win now and later: this season’s results, the age-adjusted value of the roster, '
                'and the draft picks in hand.</p><div class="panel scroll"><table class="sortable"><thead><tr><th></th><th>Team</th>'
                '<th class="n">Power</th><th class="n">All-play</th><th class="n">Roster value</th><th class="n">Picks (next 3 drafts)</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div><p class="note">Power = 45% this season’s all-play win rate, 40% roster value, '
                '15% future picks, each measured against the best team in the league. Roster value: 100 = league average (see Roster value '
                'below for how it’s figured). The tag under each team comes from the Contention window.</p>'))

    # contention window
    top = max(max(x["now"], x["next"], x["later"]) for x in rows) or 1
    body = "".join(
        f'<tr data-ap="{x["ap"]:.4f}" data-age="{x["core_age"]:.2f}" data-later="{x["later"] - x["now"]:.2f}" data-picks="{x["picks"]:.2f}">'
        f'<td class="muted rank"></td><td>{_team(x["team"])}<div style="font-size:12px" class="{TAG_CLASS.get(x["tag"], "")}">'
        f'<b>{esc(x["tag"])}</b> <span class="muted">· {esc(x["why"])}</span></div></td>'
        f'<td class="n" data-v="{x["ap"]:.4f}">{100 * x["ap"]:.0f}%</td>'
        f'<td class="n" data-v="{x["core_age"]:.2f}">{x["core_age"]:.1f}</td>'
        f'<td style="min-width:150px;font-size:12px">'
        + "".join(f'<div>{lab} {_bar(x[k], top, "pos" if k == "now" else "muted")}</div>' for k, lab in (("now", str(lg.season)), ("next", str(s0)), ("later", str(s0 + 1))))
        + f'</td><td class="n" data-v="{x["firsts_all"]}">{x["firsts"]} <span class="muted">/ {x["firsts_all"]}</span></td>'
        f'<td class="n" data-v="{x["lean"]["now"]:.2f}" style="font-size:13px"><span class="{"pos" if x["lean"]["now"] > 0 else "neg" if x["lean"]["now"] < 0 else "muted"}">'
        f'{signed(x["lean"]["now"], 1)}</span><div class="muted" style="font-size:11px;white-space:normal;min-width:160px">'
        + esc("; ".join(x["lean"]["moves"])) + '</div></td></tr>'
        for x in rows)
    out.append(("contention", "Contention window",
                '<p class="lede">Is each team built for this year or for later, and do its trades match? A team that sells and keeps '
                'winning is a failed tank; one that buys and keeps losing is a failed push.</p>'
                '<div class="panel scroll"><table class="sortable"><thead><tr><th></th><th>Team</th><th class="n">All-play</th>'
                '<th class="n">Core age</th><th>Roster strength by season</th><th class="n">1sts ' + f'{s0} / next 3</th>'
                '<th class="n">Trades: pts/gm now</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div><p class="note">Core age = average age of the ten players adding the most '
                'points per game above replacement, weighted by how much each adds. Roster strength by season = the roster as it stands, '
                'aged forward (players lose value past their position’s usual cliff and first- and second-year players grow); bars share '
                'one scale. Trades = win-now points per game above replacement (from players 26 and older) gained (+) or given away (−) in this season’s trades; young players and picks count as building for later. Tags: top '
                'third by all-play = contender (all in if buying), bottom third = rebuilding (failed push if buying), and a team that '
                'sold but sits in the top half is a failed tank.</p>'))

    # roster value
    players = sorted(vals.values(), key=lambda v: -v["value"])
    rank = {v["pid"]: i for i, v in enumerate(players, 1)}
    teams = sorted(lg.team.items(), key=lambda kv: -sum(v["value"] for v in vals.values() if v["roster"] == kv[0]))
    cards = []
    for r, name in teams:
        mine = [v for v in players if v["roster"] == r]
        tot = sum(v["value"] for v in mine)
        ages = [v["age"] for v in mine if v["age"] is not None]
        young = sum(v["value"] for v in mine if v["age"] is not None and v["age"] <= 24)
        cards.append(
            f'<tr class="xrow" tabindex="0" role="button" aria-expanded="false" aria-controls="rv-{r}" data-value="{tot:.2f}" '
            f'data-young="{young:.2f}" data-age="{(sum(ages) / len(ages) if ages else 0):.2f}">'
            f'<td class="muted rank"></td><td class="team" style="white-space:nowrap"><span class="chev" aria-hidden="true">▸</span> {esc(name)}</td>'
            f'<td class="n" data-v="{tot:.2f}">{100 * tot / avg_v:.0f}</td>'
            f'<td class="n" data-v="{young:.2f}">{100 * young / tot if tot else 0:.0f}%</td>'
            f'<td class="n" data-v="{(sum(ages) / len(ages) if ages else 0):.2f}">{(sum(ages) / len(ages) if ages else 0):.1f} '
            f'<span class="muted">→ {(sum(ages) / len(ages) + 1 if ages else 0):.1f}</span></td>'
            f'<td style="font-size:13px">' + ", ".join(f'{esc(v["name"])} <span class="muted">{v["pos"]}</span>' for v in mine[:3]) + '</td></tr>'
            f'<tr class="xdetail" id="rv-{r}" hidden><td colspan="6"><table style="font-size:13px"><thead><tr><th>Player</th><th class="n">Age now → next season</th>'
            '<th class="n">Pts/gm</th><th class="n">Over repl.</th><th class="n">Value</th><th class="n">League rank</th></tr></thead><tbody>'
            + "".join(f'<tr><td>{esc(v["name"])} <span class="muted">{v["pos"]} · {esc(v["nfl"])}</span>'
                      + (f' <span class="muted">(R{v["floor"]} pick value)</span>' if v.get("floor") else "")
                      + (' <span class="muted">· taxi</span>' if v["pid"] in lg.taxi_now.get(r, ()) else ' <span class="muted">· IR</span>' if v["pid"] in lg.reserve_now.get(r, ()) else "")
                      + f'</td><td class="n">{v["age"] if v["age"] is not None else "–"} → {v["age"] + 1 if v["age"] is not None else "–"}</td>'
                      f'<td class="n">{v["ppg"]:.1f}</td><td class="n">{signed(v["edge"], 1)}</td><td class="n">{v["value"]:.1f}</td>'
                      f'<td class="n">#{rank[v["pid"]]}</td></tr>' for v in mine if v["value"] > 0 or v["ppg"] > 0)
            + '</tbody></table></td></tr>')
    out.append(("roster-value", "Roster age and value",
                '<p class="lede">Every roster priced for the long run: what each player adds over a replacement-level starter, '
                'carried forward five seasons with his age. Click a team for its players.</p>'
                '<div class="panel scroll"><table class="expand sortable"><thead><tr><th></th><th>Team</th><th class="n">Value</th>'
                '<th class="n">From players 24 or under</th><th class="n">Avg. age now → next season</th><th>Most valuable</th></tr></thead>'
                f'<tbody>{"".join(cards)}</tbody></table></div><p class="note">Points per game blends this season with last season at half '
                f'weight. Over repl. = points per game above the player at the edge of starting in a 12-team league at his position '
                f'(QB 14th, RB 30th, WR 42nd, TE 14th). Value adds that edge over this season and the next four, each later season counting '
                f'{int(100 * DISCOUNT)}% of the one before, with players losing {int(100 * DROP)}% a year past their position’s usual cliff '
                '(QB 35, RB 27, WR 29, TE 30) and players in their first two seasons gaining 15% a year. A rookie taken in this year’s '
                'draft is worth at least what his pick was worth. 100 = league average. Ages are today’s, from Sleeper.</p>'))

    # future pick ownership
    rounds = lg.info["settings"].get("draft_rounds") or 5
    seasons_ = sorted({s for s, _, _ in own})
    by_owner = collections.defaultdict(list)
    for (s, rd, orig), o in own.items():
        by_owner[o].append((s, rd, orig))
    head = "".join(f'<th colspan="{rounds}" style="text-align:center">{s}</th>' for s in seasons_)
    sub = "".join(f'<th class="n" style="text-align:center">R{rd}</th>' for _ in seasons_ for rd in range(1, rounds + 1))
    body = []
    for r, name in sorted(lg.team.items(), key=lambda kv: -pick_total(own, pv, kv[0], s0)):
        cells = []
        for s in seasons_:
            for rd in range(1, rounds + 1):
                mine = [orig for (s2, rd2, orig) in by_owner[r] if s2 == s and rd2 == rd]
                extra = [lg.team[o] for o in mine if o != r]
                has_own = r in mine
                txt = ("✓" if has_own else '<span class="neg">–</span>') + ("".join(f' <span class="pos" title="{esc(t)}">+1</span>' for t in extra))
                cells.append(f'<td class="n" style="text-align:center;font-size:13px" title="'
                             + esc(", ".join(["own"] * has_own + [f"from {t}" for t in extra]) or "traded away") + f'">{txt}</td>')
        n1 = sum(1 for (s, rd, _) in by_owner[r] if rd == 1)
        body.append(f'<tr><td>{_team(name)}</td>{"".join(cells)}<td class="n" data-v="{n1}">{n1}</td>'
                    f'<td class="n" data-v="{len(by_owner[r])}">{len(by_owner[r])}</td></tr>')
    trades_ = []
    for (s, rd, orig), o in sorted(own.items(), key=lambda kv: (kv[0][0], kv[0][1], lg.team[kv[0][2]].lower())):
        if o != orig:
            trades_.append(f'<li>{s} round {rd}: {_team(lg.team[orig])}’s pick, now {_team(lg.team[o])}’s</li>')
    out.append(("future-picks", "Future pick ownership",
                '<p class="lede">Who holds every rookie pick in the next three drafts.</p>'
                '<div class="panel scroll"><table class="grid-t sortable"><thead><tr><th rowspan="2">Team</th>' + head
                + '<th rowspan="2" class="n">1sts</th><th rowspan="2" class="n">Picks</th></tr><tr>' + sub + '</tr></thead>'
                f'<tbody>{"".join(body)}</tbody></table></div>'
                '<details class="panel" style="margin-top:12px"><summary>Every pick that has changed hands</summary><ul style="columns:2;font-size:13px">'
                + "".join(trades_) + '</ul></details><p class="note">✓ = the team still has its own pick, – = traded away, +1 = holds '
                'another team’s pick (hover for whose). Teams are listed by the value of the picks they hold. From Sleeper’s traded picks.</p>'))

    # taxi squad and IR
    tx = taxi_rows(lg, vals)
    body = []
    for i, t in enumerate(tx):
        body.append(
            f'<tr class="xrow" tabindex="0" role="button" aria-expanded="false" aria-controls="taxi-{i}" data-value="{t["value"]:.2f}" data-stuck="{t["stuck"]:.2f}">'
            f'<td class="muted rank"></td><td class="team" style="white-space:nowrap"><span class="chev" aria-hidden="true">▸</span> {esc(t["team"])}</td>'
            f'<td style="font-size:13px">' + (", ".join(f'{esc(p["name"])} <span class="muted">{p["pos"]}</span>' for p in t["players"] if p["where"] == "Taxi") or '<span class="muted">empty</span>')
            + '</td><td style="font-size:13px">' + (", ".join(f'{esc(p["name"])}' for p in t["players"] if p["where"] == "IR") or '<span class="muted">empty</span>')
            + f'</td><td class="n" data-v="{t["stuck"]:.2f}">{t["stuck"]:.1f}</td><td class="n" data-v="{t["would"]}">{t["would"]}</td></tr>'
            f'<tr class="xdetail" id="taxi-{i}" hidden><td colspan="6"><table style="font-size:13px"><thead><tr><th>Player</th><th></th>'
            '<th class="n">Age</th><th class="n">NFL yrs</th><th class="n">Weeks stashed</th><th class="n">Pts while stashed</th>'
            '<th class="n">Weeks he’d have started</th></tr></thead><tbody>'
            + "".join(f'<tr><td>{esc(p["name"])} <span class="muted">{p["pos"]} · {esc(p["nfl"])}</span></td><td>{p["where"]}</td>'
                      f'<td class="n">{p["age"] if p["age"] is not None else "–"}</td><td class="n">{p["exp"]}</td><td class="n">{p["weeks"]}</td>'
                      f'<td class="n">{p["pts"]:.1f}</td><td class="n">{p["would"]}</td></tr>' for p in t["players"])
            + '</tbody></table></td></tr>')
    out.append(("taxi", "Taxi squad and IR",
                f'<p class="lede">Who each team is stashing ({lg.info["settings"].get("taxi_slots", 3)} taxi and '
                f'{lg.info["settings"].get("reserve_slots", 2)} IR spots), and the points they scored this season where they couldn’t count.</p>'
                '<div class="panel scroll"><table class="expand sortable"><thead><tr><th></th><th>Team</th><th>Taxi</th><th>IR</th>'
                '<th class="n">Pts while stashed</th><th class="n">Weeks a stash beat a starter</th></tr></thead>'
                f'<tbody>{"".join(body)}</tbody></table></div><p class="note">Rosters as of today. Pts while stashed = points scored in weeks '
                'the player sat on this team’s taxi squad or IR. A week counts as “beat a starter” when he outscored the lowest-scoring '
                'starter at a slot he could have filled. Teams are listed by the dynasty value on their taxi squads. Weeks before the '
                'site started saving taxi lists use today’s taxi squads.</p>'))
    return out


# ---------------------------------------------------------------- all-time
def hit_rates(sums):
    """All-time: how often each draft round (and each first-round slot) produced a starter."""
    by_season = {x["season"]: x for x in sums}
    first = min(by_season)
    # starter line by season: top 12 QB/TE/K/DEF, top 24 RB/WR, by that season's points
    tiers = {}
    for s, x in by_season.items():
        pts = x.get("season_pts") or {}
        pos = {d["pid"]: d["pos"] for y in by_season.values() for d in y.get("draft_slots") or []}
        tiers[s] = pts
    hits = {}

    def hit(pid, pos, since):
        n = {"RB": 24, "WR": 24}.get(pos, 12)
        for s in by_season:
            if s < since:
                continue
            pts = by_season[s].get("season_pts") or {}
            mine = pts.get(pid, [0, 0])[0]
            same = sorted((v[0] for p, v in pts.items() if posmap.get(p) == pos), reverse=True)
            if mine and len(same) >= n and mine >= same[n - 1]:
                return True
            if mine and len(same) < n:
                return True
        return False

    posmap = {}
    for x in sums:
        for d in x.get("draft_slots") or []:
            posmap[d["pid"]] = d["pos"]
        for o, roster in (x.get("rostered") or {}).items():
            for p, row in roster.items():
                posmap.setdefault(p, row[2])
    rows = []
    for s in sorted(by_season):
        ds = by_season[s].get("draft_slots") or []
        kind = "Startup" if s == first else "Rookie"
        rounds = collections.defaultdict(list)
        for d in ds:
            band = d["round"] if kind == "Rookie" else (d["round"] - 1) // 3 * 3 + 1
            rounds[band].append(d)
        for band, picks in sorted(rounds.items()):
            h = [d for d in picks if hit(d["pid"], d["pos"], s)]
            label = f'Round {band}' if kind == "Rookie" else (f'Rounds {band}–{min(band + 2, max(d["round"] for d in ds))}')
            rows.append({"draft": f"{s} {kind.lower()}", "label": label, "n": len(picks), "hits": len(h),
                         "best": sorted(picks, key=lambda d: -sum((by_season[y].get("season_pts") or {}).get(d["pid"], [0, 0])[0] for y in by_season if y >= s))[:3],
                         "season": s})
    slots = collections.defaultdict(list)
    for s in sorted(by_season):
        if s == first:
            continue
        for d in by_season[s].get("draft_slots") or []:
            if d["round"] == 1:
                slots[d["pick_no"]].append((s, d, hit(d["pid"], d["pos"], s)))
    return rows, slots, by_season


def hit_rates_html(sums):
    rows, slots, by_season = hit_rates(sums)
    if not rows:
        return None
    since = lambda d, s: sum((by_season[y].get("season_pts") or {}).get(d["pid"], [0, 0])[0] for y in by_season if y >= s)
    body = "".join(
        f'<tr><td>{esc(r["draft"])}</td><td>{esc(r["label"])}</td><td class="n">{r["n"]}</td><td class="n">{r["hits"]}</td>'
        f'<td class="n" data-v="{r["hits"] / r["n"]:.3f}">{100 * r["hits"] / r["n"]:.0f}% {_bar(r["hits"], r["n"])}</td>'
        f'<td style="font-size:13px">' + ", ".join(f'{esc(d["name"])} <span class="muted">{since(d, r["season"]):.0f}</span>' for d in r["best"]) + '</td></tr>'
        for r in rows)
    slot_html = ""
    if slots:
        slot_html = ('<h3 style="margin-top:16px">Rookie first round, slot by slot</h3><div class="panel scroll"><table><thead><tr><th>Pick</th>'
                     '<th>Players taken</th><th class="n">Starters</th></tr></thead><tbody>'
                     + "".join(f'<tr><td>1.{n:02d}</td><td style="font-size:13px">'
                               + ", ".join(f'{esc(d["name"])} <span class="muted">{s} · {since(d, s):.0f} pts</span>' + (" ✓" if h else "") for s, d, h in xs)
                               + f'</td><td class="n">{sum(1 for *_, h in xs if h)} of {len(xs)}</td></tr>' for n, xs in sorted(slots.items()))
                     + '</tbody></table></div>')
    return ('<p class="lede">How often each round turns up a starter, for the startup draft and every rookie draft.</p>'
            '<div class="panel scroll"><table class="sortable"><thead><tr><th>Draft</th><th>Rounds</th><th class="n">Picks</th>'
            '<th class="n">Starters</th><th class="n">Hit rate</th><th>Best picks (pts since)</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>{slot_html}<p class="note">A pick is a hit once the player has a season as a '
            'starter at his position by regular-season points among rostered players: top 24 RB or WR, top 12 QB, TE, K or defense. '
            'Startup rounds are grouped in threes. Rookie classes need time, so their hit rates will climb.</p>')


def startup_redraft_html(sums):
    by_season = {x["season"]: x for x in sums}
    first = min(by_season)
    ds = by_season[first].get("draft_slots") or []
    if not ds:
        return None
    since = lambda pid: sum((by_season[y].get("season_pts") or {}).get(pid, [0, 0])[0] for y in by_season)
    teams = {}
    for x in sums:  # most recent team name for each manager
        for o, t in (x.get("teams") or {}).items():
            teams.setdefault(o, t["team"])
    ranked = sorted(ds, key=lambda d: -since(d["pid"]))
    n = len({d["by"] for d in ds}) or 12
    body = "".join(
        f'<tr><td class="n">{i}</td><td>{esc(d["name"])} <span class="muted">{d["pos"]}</span></td>'
        f'<td class="n">{since(d["pid"]):.0f}</td><td class="n" data-v="{d["pick_no"]}">{(d["pick_no"] - 1) // n + 1}.{(d["pick_no"] - 1) % n + 1:02d}</td>'
        f'<td class="n {"pos" if d["pick_no"] > i else "neg" if d["pick_no"] < i else ""}" data-v="{d["pick_no"] - i}">{signed(d["pick_no"] - i, 0)}</td>'
        f'<td>{_team(teams.get(d["by"], "?"))}</td></tr>' for i, d in enumerate(ranked[:36], 1))
    return (f'<p class="lede">The {first} startup draft redone with everything we know now: every pick ranked by regular-season '
            'points scored since, wherever the player has played.</p><div class="panel scroll"><table class="sortable"><thead><tr>'
            '<th class="n">Redraft</th><th>Player</th><th class="n">Pts since</th><th class="n">Taken</th><th class="n">Moved</th><th>Drafted by</th>'
            f'</tr></thead><tbody>{body}</tbody></table></div><p class="note">Top 36 shown. Moved = places he climbs (+) or falls (−) '
            'from his real pick. Points don’t account for age, so an old star ranks high here even if his dynasty value has faded.</p>')


def pick_ledger_html(sums):
    """Every trade that moved a rookie pick, and who the pick became."""
    by_season = {x["season"]: x for x in sums}
    teams = {}
    for x in sums:
        for o, t in (x.get("teams") or {}).items():
            teams.setdefault(o, t["team"])
    since = lambda pid, s: sum((by_season[y].get("season_pts") or {}).get(pid, [0, 0])[0] for y in by_season if y >= s)
    rows = []
    for x in sorted(sums, key=lambda x: x["season"]):
        for t in x.get("trades") or []:
            for sd in t["sides"]:
                for pk in sd.get("pick_list") or []:
                    others = [o for o in t["sides"] if o is not sd]
                    hit = next((d for d in (by_season.get(pk["season"]) or {}).get("draft_slots") or []
                                if d["round"] == pk["round"] and d["orig"] == pk["orig"]), None)
                    gave = "; ".join(", ".join([g["player"] for g in o["got"]] + [f'{p["season"]} R{p["round"]}' for p in o.get("pick_list") or []])
                                     for o in others)
                    rows.append({"when": f'{x["season"]} wk {t["week"] or "–"}', "key": (x["season"], t["week"] or 0),
                                 "pick": f'{pk["season"]} R{pk["round"]}', "orig": teams.get(pk["orig"], "?"),
                                 "to": sd["team"], "from": ", ".join(o["team"] for o in others), "for": gave,
                                 "became": hit, "pts": since(hit["pid"], pk["season"]) if hit else None})
    if not rows:
        return None
    rows.sort(key=lambda r: r["key"], reverse=True)
    body = "".join(
        f'<tr><td data-v="{r["key"][0]}{r["key"][1]:02d}">{esc(r["when"])}</td><td>{esc(r["pick"])} <span class="muted">· originally {esc(r["orig"])}</span></td>'
        f'<td>{_team(r["to"])}</td><td>{_team(r["from"])}<div class="muted" style="font-size:12px">and got {esc(r["for"]) or "nothing back"}</div></td>'
        f'<td data-v="{r["pts"] if r["pts"] is not None else -1:.1f}">'
        + (f'{esc(r["became"]["name"])} <span class="muted">{r["became"]["pos"]} · {r["pts"]:.0f} pts</span>' if r["became"] else '<span class="muted">not used yet</span>')
        + '</td></tr>' for r in rows)
    return ('<p class="lede">Every trade that moved a rookie pick, newest first, and who the pick turned into.</p>'
            '<div class="panel scroll"><table class="sortable"><thead><tr><th>When</th><th>Pick</th><th>Got the pick</th><th>Sent it</th>'
            f'<th>Became</th></tr></thead><tbody>{body}</tbody></table></div><p class="note">A pick that moved more than once shows '
            'once per trade. Points = regular-season points the drafted player has scored since his rookie season began.</p>')
