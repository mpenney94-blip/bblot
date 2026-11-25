# BBLOT dashboard

Stats site for the Boys Boys Lots of Toys dynasty league on Sleeper: https://mpenney94-blip.github.io/bblot/

Built by GitHub Actions (Tuesday 4am and Thursday noon Pacific, plus every push to `main`) and served on GitHub Pages.
The current season is at the root, finished seasons at `/<season>/`, and the all-time page at `/alltime/`. Only the
dynasty seasons (2025 on) are included.

Build locally:

```
python3 -m dashboard.build 1330268610945376256 --out _site --no-winprob
```

The written columns use Claude when the `ANTHROPIC_API_KEY` repo secret is set, and a built-in writer otherwise.
League lore for the columns goes in `dashboard/league_notes.md`.
