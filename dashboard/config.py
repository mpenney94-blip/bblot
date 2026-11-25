"""League-specific settings for this site (Boys Boys Lots of Toys, a dynasty league)."""

LEAGUE_ID = "1330268610945376256"
LEAGUE_SHORT = "BBLOT"

# The league ran as redraft before 2025; only the dynasty seasons count here.
FIRST_SEASON = 2025

# Playoffs: each division winner gets a bye, then the next best records regardless of division fill the remaining spots.
# Every tie goes to points for.
PLAYOFF_FORMAT = "division_winners_bye_then_wildcards"

# Managers who go by she/her (Sleeper owner ids). Everyone in this league is he/him.
SHE = set()


def league_chain(client, league_id):
    """[(league_id, league info)] from this league back through Sleeper's previous_league_id chain, dynasty seasons only."""
    out = []
    while league_id and league_id != "0":
        info = client.league(league_id)
        if int(info["season"]) < FIRST_SEASON:
            break
        out.append((league_id, info))
        league_id = info.get("previous_league_id")
    return out


# FAAB went from $100 (2025) to $1000 (2026), so waiver money is shown as a share of that season's budget.
def pct_text(v, budget):
    """A FAAB amount as a share of the season's budget: '13%', or '0.5%' for small amounts."""
    p = 100 * v / budget if budget else 0
    return f"{p:.1f}%" if 0 < p < 10 and round(p, 1) != round(p) else f"{p:.0f}%"


def money_text(v, budget):
    """A single bid: share of the budget, then the dollars: '13% ($130)'."""
    return f"{pct_text(v, budget)} (${v:,})"
