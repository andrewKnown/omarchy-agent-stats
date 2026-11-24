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
activity pulse on or off, opens the Agents panel, and refreshes. Choices are
saved to the widget's entry in `~/.config/omarchy/shell.json` through
`omarchy bar set`. You can also toggle the menu from a keybinding or script:

```bash
omarchy-shell io.github.andrewknown.agent-stats toggle
```

## How it works

The widget does no collection of its own. It reads the usage records that
Omarchy's built-in **Agents** plugin (`omarchy.agents`) keeps in
`~/.local/state/omarchy/agents/usage/`, through a small bundled Python script
(`read.py`). It makes no network requests and only reads local files (plus
`/proc` when the activity pulse is on).

Clicking an agent runs `launch.sh`, which starts that agent through Omarchy's
own `omarchy-agent` launcher, so it opens in the same terminal with the same
flags and active account as `Super` launches. Your default agent setting is
never changed. Agents without a CLI (gateways and prepaid APIs) open the
Agents panel instead.

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
| `warnAt` | `80` | Percentage at which a meter turns urgent |
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
