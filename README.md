# Omarchy Agent Stats

Your AI coding subscriptions' rate limits and today's token count, right in the
Omarchy bar.

![Agent Stats in the bar](preview.png)

Each subscription gets a two-letter badge (`CL` Claude, `CX` Codex, `GK` Grok,
…) with two thin meters: the short session window (5-hour) on top and the
weekly window below. A meter turns the bar's urgent color at 80% by default.
An agent whose limits couldn't be refreshed is shown faded. After the meters,
`Σ 35.9M` is the total tokens used today across every agent.

| Action | On an agent | On the token total |
|---|---|---|
| Hover | Each limit's % and time to reset | Today's tokens per agent |
| Left click | Open that agent in a terminal | Open the Agents panel |
| Right click | Open the menu | Open the menu |
| Middle click | Refresh usage now | Refresh usage now |

The right-click menu switches the badge between letters and logos, turns the
activity pulse on or off, sets the low usage alert and its threshold, sets what prepaid agents show,
the low balance alert and live OpenRouter checks (shown once one is set up),
turns the Claude extra-usage check on or off, sets the update frequency, opens
the Agents panel, and refreshes. Choices are
saved to the widget's entry in `~/.config/omarchy/shell.json` through
`omarchy bar set`. You can also toggle the menu from a keybinding or script:

```bash
omarchy-shell io.github.andrewknown.agent-stats toggle
```

## How it works

The widget does no collection of its own. It reads the usage records that
Omarchy's built-in **Agents** plugin (`omarchy.agents`) keeps in
`~/.local/state/omarchy/agents/usage/`, through a small bundled Python script
(`read.py`). By default it makes no network requests and only reads local
files (plus `/proc` when the activity pulse is on).

## How fresh the numbers are

Omarchy's Agents plugin refreshes the usage records on its own schedule, every
15 minutes by default, and the widget re-reads them every `refreshIntervalSec`
(60 s). **Update frequency** (`updateFrequency`, in the menu) asks for fresher
numbers, best effort:

| Choice | Subscription limits | Claude extra usage | OpenRouter (live) |
|---|---|---|---|
| `Off` (default) | Omarchy's schedule | every 5 min | every minute |
| `Max` | Claude every 3 min, other agents every minute | every 3 min | every minute |
| `3m`, `5m`, `10m`, `15m` | that interval | that interval | that interval |

`Max` is the shortest interval each provider allows. Anthropic refuses usage
checks more often than about every 3 minutes, the floor Omarchy itself keeps;
OpenAI (Codex) and xAI (Grok) publish no limit, so a minute is best effort. With
`Max` the records are also re-read every 30 seconds.

Limits are refreshed by running Omarchy's own `omarchy agent usage-update
--limits-only` for just the agents shown, the cheap refresh the Agents panel
does when it opens; a collector may still reuse an answer from the last few
seconds. Today's token total (Σ) follows Omarchy's full runs, which rescan
session files; shorten those with `omarchy bar set omarchy.agents
refreshIntervalSec 300 --json`. Middle-click forces a full refresh at any time.

## Prepaid balances (OpenRouter)

Prepaid accounts that Omarchy tracks (OpenRouter, DeepSeek, Fireworks) get a
section showing the remaining balance, or what was spent today or this month
(`prepaidText`). The ring turns on when the balance reaches `lowBalanceAt`
dollars. Hover shows the balance, spend today, this week (live only) and
this month, and any key limit.

With live OpenRouter checks and a management key that can read analytics, the
tooltip also shows account-wide tokens reported today, this week (starting
Monday), and this month in UTC. Analytics also supplies today's and this
month's spend, the daily average over completed days this month, and the
monthly average over the last three completed calendar months. Select
**OpenRouter API** in the widget's right-click menu to enable live checks, or
**Local estimates** to use Omarchy's saved records.

Omarchy needs a key for OpenRouter. Put it in
`~/.config/omarchy/agents/openrouter.json` as `{"apiKey": "sk-or-..."}`. Add a
`managementKey` from openrouter.ai/settings/management-keys as well for
account-wide live spend (below), and in case OpenRouter won't give your API
key the balance.

Spend can come from two places:

- **Omarchy (default).** Omarchy records only the all-time total spent, so the
  widget keeps a snapshot of it at the start of each day and month in
  `~/.local/state/omarchy-agent-stats/spend.json`. No network and no keys. It
  starts counting when installed, so the first day and month show "since"
  times. Spend is account-wide.
- **Live (`openrouterLive`, off by default).** The widget asks openrouter.ai
  itself, at most once a minute. With a management key it sums every key's
  spend today, this week and this month from `GET /api/v1/keys`, so the
  numbers cover the whole account (UTC days, weeks starting Monday; keys
  deleted this period drop out of it). With only an API key, it reads that
  key's own spend and credit limit from `GET /api/v1/key`. The balance comes
  from `GET /api/v1/credits`, with whichever key OpenRouter accepts there. It
  reads the same key file as Omarchy (or `OPENROUTER_API_KEY` /
  `OPENROUTER_MANAGEMENT_KEY` in the environment) and sends the keys only to
  openrouter.ai. The last answer is cached in
  `~/.local/state/omarchy-agent-stats/openrouter-live.json` (numbers only,
  never a key); a failed check keeps it, marked stale.

## Claude extra usage

When a Claude plan limit is used up, an account with extra usage turned on
keeps working on pay-as-you-go credits. With `claudeExtra` on (off by
default), the widget shows that month's extra-usage credit: on hover always
("Extra usage left: $32.74 (55%)", then what has been spent of the monthly
limit), and next to Claude's meters while a plan limit is at 100% (`$32.74 55%`,
or the amount spent with `extraText: Spent`), with "on extra usage until …" in
the tooltip. While on extra usage the low usage ring follows the credit left
(the same `lowAlertAt` %) instead of the used-up plan limit. Without extra usage, the tooltip says when the agent is blocked
until instead.

Omarchy doesn't record this, so the widget asks Anthropic itself: the same
`https://api.anthropic.com/api/oauth/usage` endpoint Claude Code's `/usage` and
Omarchy's own collector read, at most every five minutes, with the access token
Claude Code keeps in `$CLAUDE_CONFIG_DIR/.credentials.json` (default
`~/.claude`). The token is sent only to api.anthropic.com and never refreshed
or stored; while it has lapsed the check is skipped until Claude Code next runs.
The numbers are cached in `~/.local/state/omarchy-agent-stats/claude-extra.json`.

## Requirements

- Omarchy with the built-in Agents plugin (on by default), signed in to at
  least one subscription (`omarchy agent account add`)
- `python3` (included in Omarchy)

## Install

```bash
omarchy plugin add https://github.com/andrewKnown/omarchy-agent-stats --enable
```

It's added to the right side of the bar. Move it next to the Agents icon with:

```bash
omarchy bar move io.github.andrewknown.agent-stats --after omarchy.agents
```

## Settings

Set these on the widget's entry in `~/.config/omarchy/shell.json`, or with
`omarchy bar set io.github.andrewknown.agent-stats <key> <value>`:

| Key | Default | Meaning |
|---|---|---|
| `badgeStyle` | `Letters` | `Letters` (CL, CX, GK) or `Logos`, the agents' logos from Omarchy's Agents plugin |
| `activityPulse` | `false` | Gently pulse an agent's badge while its CLI is working |
| `lowAlert` | `Off` | `Ring` draws a ring in the bar's urgent colour around an agent when any of its limits is nearly used up; `Pulse` makes that ring breathe |
| `lowAlertAt` | `10` | Remaining percentage (1–50) at or below which the ring appears. The menu offers 5, 10, 20 and 30; any other value can be set here |
| `prepaidText` | `Balance` | What a prepaid agent shows: `Balance`, `Today` (spent today) or `Month` (spent this month) |
| `lowBalanceAt` | `5` | Dollars of balance at or below which a prepaid agent gets the ring; `0` turns it off |
| `openrouterLive` | `false` | Ask openrouter.ai directly for exact spend and balance (see above) |
| `claudeExtra` | `false` | Ask Anthropic for Claude's extra-usage spend this month (see above) |
| `extraText` | `Left` | While on extra usage, show the credit `Left` ($ and %) or the amount `Spent` |
| `warnAt` | `80` | Percentage at which a meter turns urgent |
| `updateFrequency` | `Off` | `Off`, `Max`, `3m`, `5m`, `10m` or `15m` (see "How fresh the numbers are") |
| `refreshIntervalSec` | `60` | How often the local usage files are re-read |

**Logos** are read from Omarchy's own install
(`$OMARCHY_PATH/shell/plugins/agents/assets/`) rather than bundled here. If
one is missing, that agent shows its letters instead.

**Activity pulse:** every 3 seconds `activity.py` samples the CPU time of
running agent CLIs (`claude`, `codex`, `grok`, …) from `/proc` over one second.
An agent counts as working once its processes use more than about 3% of one
core, so a session that's open but idle doesn't pulse. It only reads `/proc`
and runs only while the option is on.

The usage itself is refreshed by the Agents plugin (every 15 minutes, or
faster near a limit). Middle click asks it to refresh right away.

## Remove

```bash
omarchy plugin remove io.github.andrewknown.agent-stats
```

## License

MIT
