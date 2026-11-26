import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// One small meter pair per agent subscription: a letter, then the short
// (5-hour) window on top and the longest window below. It only reads the
// records omarchy-agent-usage-update writes (via read.py). Click an agent to
// open it in a terminal (launch.sh), right click for this widget's menu
// (bar layout, sort, badge style, Agents panel, refresh), middle click to refresh.
// Compact / Alerts / Summary shrink the bar; the Usage panel lists everyone.
//
// The root is Omarchy's popup base (Panel) rather than BarWidget so the menu
// can be a standard bar popup; it carries the same bar/settings API.
Panel {
  id: root
  moduleName: "io.github.andrewknown.agent-stats"
  ipcTarget: "io.github.andrewknown.agent-stats"

  readonly property bool vertical: bar ? bar.vertical : false
  readonly property int barSize: bar && bar.barSize ? bar.barSize : 26

  // Settings are saved through Omarchy's own CLI, which writes this widget's
  // entry in shell.json; the shell hot-reloads it back into `settings`.
  function saveSetting(key, value, json) {
    var cmd = ["omarchy-bar", "set", root.moduleName, key, String(value)]
    if (json) cmd.push("--json")
    Quickshell.execDetached(cmd)
  }

  property var agents: []
  // Providers hidden from the bar (hiddenAgents: comma-separated ids, e.g.
  // "grok,kimi"); the menu's Show in bar row lists them all.
  readonly property var hiddenAgents: String(setting("hiddenAgents", "") || "").split(",")
    .map(function(x) { return x.trim() }).filter(function(x) { return /^[a-z][a-z0-9-]{0,23}$/.test(x) })
  readonly property var visibleAgents: agents.filter(function(a) { return hiddenAgents.indexOf(String(a.id)) === -1 })
  function setHidden(id, hide) {
    var list = hiddenAgents.filter(function(x) { return x !== id })
    if (hide) list.push(id)
    saveSetting("hiddenAgents", list.join(","), false)
  }
  // The provider setup wizard (providers.py) in Omarchy's floating terminal.
  readonly property string providersScript: Qt.resolvedUrl("providers.py").toString().replace("file://", "")
  function openProviders() {
    var quoted = "'" + providersScript.replace(/'/g, "'\\''") + "'"
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", "python3 -I " + quoted])
  }
  property var busyIds: []
  readonly property bool useLogos: String(setting("badgeStyle", "Letters")) === "Logos"
  readonly property var barModes: ["Full", "Compact", "Alerts", "Summary"]
  readonly property string barMode: {
    var v = String(setting("barMode", "Full"))
    return barModes.indexOf(v) !== -1 ? v : "Full"
  }
  readonly property var sortOrders: ["Name", "Critical", "Calm"]
  readonly property string sortOrder: {
    var v = String(setting("sortOrder", "Name"))
    return sortOrders.indexOf(v) !== -1 ? v : "Name"
  }
  readonly property bool showTodayTotal: setting("showTodayTotal", true) !== false
  // Compact and Alerts only draw badges in the bar; Full keeps meters.
  readonly property bool iconsOnly: barMode === "Compact" || barMode === "Alerts"
  readonly property bool summaryMode: barMode === "Summary"
  readonly property bool activityPulse: setting("activityPulse", false) === true
  // Low usage alert: a ring around an agent once any of its limits has this
  // much or less remaining. "Ring" is steady, "Pulse" breathes.
  readonly property string lowAlert: {
    var v = String(setting("lowAlert", "Off"))
    return v === "Ring" || v === "Pulse" ? v : "Off"
  }
  readonly property real lowAlertAt: Math.max(1, Math.min(50, Number(setting("lowAlertAt", 10)) || 10))
  // The tightest limit (least remaining) of an agent, as remaining percent.
  function remaining(agent) {
    var least = 100
    for (var i = 0; i < agent.limits.length; i++)
      least = Math.min(least, 100 - Number(agent.limits[i].percent))
    return Math.max(0, least)
  }
  function limitLow(agent) { return agent.limits.length > 0 && remaining(agent) <= lowAlertAt }
  // Prepaid balances (OpenRouter and the like): dollars, not percent.
  readonly property real lowBalanceAt: Math.max(0, Number(setting("lowBalanceAt", 5)) || 0)
  function balanceLow(agent) {
    return !!agent.prepaid && lowBalanceAt > 0 && agent.prepaid.remaining !== null
      && agent.prepaid.remaining !== undefined && Number(agent.prepaid.remaining) <= lowBalanceAt
  }
  // Near a limit / low balance — drives Alerts mode and Summary, even when
  // the optional ring style is Off.
  function nearLimit(agent) {
    if (balanceLow(agent)) return true
    if (onExtra(agent) && extraLeftPct(agent.extra) !== null) return extraLow(agent)
    return limitLow(agent)
  }
  // Lower = more urgent (Critical sort). Prepaid-low sorts ahead of everyone.
  function urgency(agent) {
    if (balanceLow(agent)) return -1
    if (onExtra(agent) && extraLeftPct(agent.extra) !== null) return extraLeftPct(agent.extra)
    if (agent.limits && agent.limits.length > 0) return remaining(agent)
    return 100
  }
  function sortAgents(list) {
    var copy = list.slice()
    copy.sort(function(a, b) {
      if (root.sortOrder === "Critical") {
        var d = root.urgency(a) - root.urgency(b)
        return d !== 0 ? d : String(a.name).localeCompare(String(b.name))
      }
      if (root.sortOrder === "Calm") {
        var e = root.urgency(b) - root.urgency(a)
        return e !== 0 ? e : String(a.name).localeCompare(String(b.name))
      }
      return String(a.name).localeCompare(String(b.name))
    })
    return copy
  }
  readonly property var usageAgents: sortAgents(visibleAgents)
  readonly property var barAgents: {
    if (summaryMode) return []
    var list = visibleAgents
    if (barMode === "Alerts") list = list.filter(function(a) { return nearLimit(a) })
    return sortAgents(list)
  }
  readonly property int alertCount: {
    var n = 0
    for (var i = 0; i < visibleAgents.length; i++) if (nearLimit(visibleAgents[i])) n++
    return n
  }
  readonly property real worstRemaining: {
    var least = 100
    var any = false
    for (var i = 0; i < visibleAgents.length; i++) {
      if (!nearLimit(visibleAgents[i])) continue
      any = true
      var u = urgency(visibleAgents[i])
      least = Math.min(least, u < 0 ? 0 : u)
    }
    return any ? least : 100
  }
  // On extra usage the plan limit is already spent, so "low" means the extra
  // usage credit left this month, by the same % threshold.
  function isLow(agent) {
    if (lowAlert === "Off") return false
    return nearLimit(agent)
  }
  // Which popup the KeyboardPanel is showing (menu vs usage list).
  property string popupKind: "menu"
  function openPopup(kind) {
    popupKind = kind
    if (!opened) open()
  }
  function toggleMenu() {
    if (opened && popupKind === "menu") close()
    else openPopup("menu")
  }
  function toggleUsage() {
    if (opened && popupKind === "usage") close()
    else openPopup("usage")
  }
  function refreshNow() {
    Quickshell.execDetached(["omarchy-shell", "omarchy.agents", "refresh"]); root.runRemoteChecks()
    refreshLater.restart()
    root.refresh()
  }
  function activateAgent(agent) {
    var launcher = launchers[agent.id]
    if (!launcher && webPages[agent.id]) Quickshell.execDetached(["xdg-open", webPages[agent.id]])
    else if (!launcher) openPanel()
    else launch(agent.id, launcher)
  }
  function barPress(button, agent) {
    if (button === Qt.MiddleButton) refreshNow()
    else if (button === Qt.RightButton) toggleMenu()
    else if (agent) activateAgent(agent)
    else if (barMode === "Full") openPanel()
    else toggleUsage()
  }

  readonly property string prepaidText: {
    var v = String(setting("prepaidText", "Balance"))
    return v === "Today" || v === "Month" ? v : "Balance"
  }
  readonly property bool openrouterLive: setting("openrouterLive", false) === true
  readonly property bool claudeExtra: setting("claudeExtra", false) === true

  // A plan limit at 100% blocks the agent until it resets, unless the account
  // has extra usage (pay-as-you-go) to carry on with.
  function fullLimits(agent) {
    var out = []
    for (var i = 0; i < agent.limits.length; i++) if (Number(agent.limits[i].percent) >= 100) out.push(agent.limits[i])
    return out
  }
  function blockedUntil(agent) {
    var full = fullLimits(agent), latest = ""
    for (var i = 0; i < full.length; i++)
      if (full[i].resetsAt && (!latest || Date.parse(full[i].resetsAt) > Date.parse(latest))) latest = full[i].resetsAt
    return latest
  }
  readonly property string extraText: String(setting("extraText", "Left")) === "Spent" ? "Spent" : "Left"
  function extraLeft(x) {
    return x && x.limit && x.used !== undefined && x.used !== null ? Math.max(0, Number(x.limit) - Number(x.used)) : null
  }
  function extraLeftPct(x) {
    var left = extraLeft(x)
    return left === null ? null : Math.round(100 * left / Number(x.limit))
  }
  function extraLow(agent) {
    var pct = extraLeftPct(agent.extra)
    return pct !== null && pct <= lowAlertAt
  }
  function extraLabel(x) {
    if (x.reached === true || x.enabled !== true)
      return extraText === "Spent" ? money(x.used, x.currency) : money(0, x.currency) + " 0%"
    if (extraText === "Left" && extraLeft(x) !== null)
      return money(extraLeft(x), x.currency) + " " + extraLeftPct(x) + "%"
    return money(x.used, x.currency)
  }
  // Shown next to the meters whenever a plan limit is full and the account has
  // extra usage figures: what's left while credits carry on, and $0.00 0% in
  // red once the cap is reached or extra usage is off.
  function showExtra(agent) {
    return !!agent.extra && agent.extra.used !== undefined && agent.extra.used !== null && fullLimits(agent).length > 0
  }
  function extraSpent(agent) {
    return !!agent.extra && (agent.extra.reached === true || agent.extra.enabled !== true)
  }
  function onExtra(agent) {
    return !!agent.extra && agent.extra.enabled === true && agent.extra.reached !== true && fullLimits(agent).length > 0
  }
  function resetText(iso) {
    var t = Date.parse(iso)
    if (isNaN(t)) return ""
    var d = new Date(t)
    return (d.toDateString() === new Date().toDateString() ? "" : Qt.formatDate(d, "ddd ")) + Qt.formatTime(d, "hh:mm")
  }
  readonly property bool hasPrepaid: {
    for (var i = 0; i < agents.length; i++) if (agents[i].prepaid) return true
    return false
  }
  function money(value, currency) {
    if (value === null || value === undefined || isNaN(Number(value))) return "–"
    var n = Number(value)
    var sign = !currency || currency === "USD" ? "$" : currency + " "
    if (n >= 10000) return sign + (n / 1000).toFixed(0) + "K"
    if (n >= 1000) return sign + (n / 1000).toFixed(1) + "K"
    if (n >= 100) return sign + n.toFixed(0)
    return sign + n.toFixed(2)
  }
  function prepaidLabel(p) {
    if (prepaidText === "Today") return money(p.today, p.currency) + "/d"
    if (prepaidText === "Month") return money(p.month, p.currency) + "/mo"
    return money(p.remaining, p.currency)
  }
  function sinceText(iso) {
    var t = Date.parse(iso)
    if (isNaN(t)) return ""
    var d = new Date(t)
    return d.toDateString() === new Date().toDateString()
      ? Qt.formatTime(d, "hh:mm") : Qt.formatDate(d, "d MMM")
  }

  // Omarchy's Agents plugin ships each agent's logo as assets/<id>.svg, with
  // an <id>-light.svg twin for light bars where the logo is white.
  readonly property string logoDir: (Quickshell.env("OMARCHY_PATH") || "/usr/share/omarchy") + "/shell/plugins/agents/assets/"
  readonly property bool lightBar: (0.299 * fg.r + 0.587 * fg.g + 0.114 * fg.b) < 0.5
  function logoUrl(id) {
    if (!/^[a-z0-9-]{1,24}$/.test(String(id))) return ""
    // Entries this plugin adds itself carry their own logo in assets/.
    if (ownLogos[id]) return Qt.resolvedUrl("assets/" + id + (lightBar ? "-light" : "") + ".svg")
    return "file://" + logoDir + id + (lightBar && lightLogos[id] ? "-light" : "") + ".svg"
  }
  // Entries this plugin adds itself, with a logo bundled in assets/ (Simple
  // Icons, CC0) since Omarchy's set has none for them.
  readonly property var ownLogos: ({ "gemini-api": true })
  // Agents whose logo has a separate light-bar version.
  readonly property var lightLogos: ({ agy: true, codex: true, copilot: true, cursor: true, devin: true,
    grokbot: true, grok: true, hermes: true, kimi: true, minimax: true, muse: true, ollama: true,
    opencode: true, openrouter: true, synthetic: true, zai: true })

  Process {
    id: activityReader
    command: ["python3", "-I", Qt.resolvedUrl("activity.py").toString().replace("file://", "")]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(text)
          root.busyIds = Array.isArray(parsed) ? parsed : []
        } catch (e) {
          root.busyIds = []
        }
      }
    }
  }

  // Only runs while the pulse is switched on and there's an agent to show.
  Timer {
    interval: 3000
    repeat: true
    running: root.activityPulse && root.visibleAgents.length > 0
    triggeredOnStart: true
    onTriggered: if (!activityReader.running) activityReader.running = true
    onRunningChanged: if (!running) root.busyIds = []
  }
  property real todayTokens: 0
  property var todayByAgent: []
  readonly property int intervalMs: Math.max(15, Number(setting("refreshIntervalSec", 60))) * 1000
  readonly property real warnAt: Number(setting("warnAt", 80))

  readonly property color fg: bar && bar.foreground !== undefined ? bar.foreground : "#e0e0e0"
  readonly property color hot: bar && bar.urgent !== undefined ? bar.urgent : "#e06c75"
  readonly property string font: bar && bar.fontFamily ? bar.fontFamily : "monospace"

  readonly property string scriptPath: Qt.resolvedUrl("read.py").toString().replace("file://", "")

  function refresh() {
    if (!reader.running) reader.running = true
  }

  // Two-letter badges so Claude and Codex don't both read "C"; any other agent
  // falls back to the first two letters of its name.
  readonly property var badges: ({ claude: "CL", codex: "CX", grok: "GK", cursor: "CU", copilot: "CP", "gemini-api": "GM" })
  function badge(agent) {
    return badges[agent.id] || String(agent.name).replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase()
  }

  function shortLimit(limits) { return limits[0] }
  function longLimit(limits) { return limits[limits.length - 1] }

  function untilText(iso) {
    var t = Date.parse(iso)
    if (isNaN(t)) return ""
    var mins = Math.max(0, Math.round((t - Date.now()) / 60000))
    if (mins < 60) return mins + "m"
    var hours = Math.floor(mins / 60)
    if (hours < 48) return hours + "h " + (mins % 60) + "m"
    return Math.floor(hours / 24) + "d " + (hours % 24) + "h"
  }

  Process {
    id: reader
    command: {
      var cmd = ["python3", "-I", root.scriptPath]
      if (root.openrouterLive) cmd.push("--openrouter-live")
      if (root.claudeExtra) cmd.push("--claude-extra")
      cmd.push("--openrouter-age=" + root.openrouterAgeSec, "--claude-age=" + root.claudeAgeSec)
      if (root.geminiProjects) cmd.push("--gemini-projects=" + root.geminiProjects, "--gemini-age=" + root.geminiAgeSec)
      return cmd
    }
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(text)
          root.agents = parsed && Array.isArray(parsed.agents) ? parsed.agents : []
          root.todayTokens = parsed && Number(parsed.todayTokens) > 0 ? Number(parsed.todayTokens) : 0
          root.todayByAgent = parsed && Array.isArray(parsed.todayByAgent) ? parsed.todayByAgent : []
        } catch (e) {
          root.agents = []
          root.todayTokens = 0
          root.todayByAgent = []
        }
      }
    }
  }

  // Update frequency, one setting for every source (best effort):
  //  Off  - Omarchy's own schedule for limits and tokens; OpenRouter live
  //         every minute, Claude extra usage every 5 minutes.
  //  Max  - the shortest interval each provider allows, per provider:
  //         Claude every 3 minutes (Anthropic refuses faster probes; Omarchy
  //         keeps the same floor), Claude extra usage every 3, every other
  //         agent's limits and OpenRouter every minute, and the records
  //         re-read every 30 seconds.
  //  3m.. - every source at that interval.
  // Limits are refreshed by running Omarchy's own updater with --limits-only
  // for just the agents shown, the cheap refresh its Agents panel does when
  // opened. Today's token total still follows Omarchy's own full runs.
  readonly property var frequencies: ["Off", "Max", "3m", "5m", "10m", "15m"]
  readonly property string updateFrequency: {
    var v = String(setting("updateFrequency", "Off"))
    return frequencies.indexOf(v) !== -1 ? v : "Off"
  }
  readonly property bool maxFrequency: updateFrequency === "Max"
  // Minutes between limit refreshes this widget asks for (0 = Omarchy's).
  readonly property int nudgeMin: maxFrequency ? 1 : updateFrequency === "Off" ? 0 : parseInt(updateFrequency)
  // Providers whose limits can't be asked for as often as a minute, and the
  // shortest gap (minutes) they tolerate; used by Max.
  // Claude: Omarchy documents 3 minutes, but measured, Anthropic refuses
  // checks that often once Omarchy's own (every 3 min near a limit, plus its
  // regular run) are counted, so 5 is the shortest that gets answered.
  readonly property var providerFloorMin: ({ claude: 5 })
  property var lastNudge: ({})
  // Max: 30 s, the floor remote.py keeps for OpenRouter; Off: about a minute.
  readonly property int openrouterAgeSec: maxFrequency ? 30 : nudgeMin > 0 ? nudgeMin * 60 : 55
  // Never under 5 minutes: shares Anthropic's rate limit with Omarchy's checks.
  readonly property int claudeAgeSec: Math.max(300, nudgeMin > 0 && !maxFrequency ? nudgeMin * 60 : 300)
  readonly property int geminiAgeSec: maxFrequency ? 60 : nudgeMin > 0 ? nudgeMin * 60 : 300
  // Google Cloud projects whose Gemini API usage to show (set from the
  // Providers screen); empty = no Gemini check.
  readonly property string geminiProjects: String(setting("geminiProjects", "") || "").split(",")
    .map(function(x) { return x.trim() }).filter(function(x) { return /^[a-z][a-z0-9-]{5,29}$/.test(x) }).join(",")
  // The network checks (remote.py) each run in the background on their own
  // timer and leave a cache file; read.py only reads those, so a slow or stuck
  // check never holds up the bar. A finished check re-reads at once.
  readonly property string remoteScript: Qt.resolvedUrl("remote.py").toString().replace("file://", "")
  component RemoteCheck: Item {
    id: check
    property bool enabled: false
    property int ageSec: 300
    property var args: []
    function run(force) {
      if (!enabled || proc.running) return
      proc.command = ["python3", "-I", root.remoteScript].concat(check.args).concat(force === true ? ["--force"] : [])
      proc.running = true
    }
    Process {
      id: proc
      // 75: put off to keep clear of Omarchy's own Claude check; retry soon.
      onExited: function(code) { if (code === 75) retrySoon.restart(); else root.refresh() }
    }
    Timer {
      id: retrySoon
      interval: 30000
      onTriggered: check.run()
    }
    // Asks at least once a minute whether the check is due (remote.py skips
    // it until it is), so a long interval isn't stretched by up to a whole
    // extra round when the timer and the last answer fall out of step.
    Timer {
      interval: (Math.min(check.ageSec, 60) + 3) * 1000
      repeat: true
      running: check.enabled
      triggeredOnStart: true
      onTriggered: check.run()
    }
  }
  RemoteCheck {
    id: openrouterCheck
    enabled: root.openrouterLive
    ageSec: root.openrouterAgeSec
    args: ["openrouter", "--openrouter-age=" + root.openrouterAgeSec]
  }
  RemoteCheck {
    id: claudeCheck
    enabled: root.claudeExtra
    ageSec: root.claudeAgeSec
    args: ["claude", "--claude-age=" + root.claudeAgeSec]
  }
  RemoteCheck {
    id: geminiCheck
    enabled: root.geminiProjects !== ""
    ageSec: root.geminiAgeSec
    args: ["gemini", "--gemini-projects=" + root.geminiProjects, "--gemini-age=" + root.geminiAgeSec]
  }
  // A refresh someone asked for (middle click, Refresh) skips the freshness wait.
  function runRemoteChecks() { openrouterCheck.run(true); claudeCheck.run(true); geminiCheck.run(true) }

  readonly property int readMs: maxFrequency ? 30000 : intervalMs
  Process {
    id: nudger
    onExited: root.refresh()
  }
  Timer {
    // Checks every 10 seconds which agents are due, so each keeps its own
    // pace (a 1-minute agent refreshes every 60-70 s) however early or late
    // the shell's timers fire.
    interval: 10000
    repeat: true
    running: root.nudgeMin > 0
    onTriggered: {
      if (nudger.running) return
      var now = Date.now()
      // A full refresh, not --limits-only: that one reuses the token scan for
      // up to 15 minutes, which left Σ tokens behind the meters.
      var cmd = ["omarchy-agent-usage-update"]
      // The agents in the bar, plus any hidden (or meter-less) one with tokens
      // today, so Σ keeps up with every agent it adds up.
      var due = root.visibleAgents.map(function(a) { return { id: String(a.id), checkedAt: a.checkedAt } })
      for (var t = 0; t < root.todayByAgent.length; t++) {
        var tid = String(root.todayByAgent[t].id || "")
        if (tid && !due.some(function(d) { return d.id === tid })) due.push({ id: tid, checkedAt: 0 })
      }
      for (var i = 0; i < due.length; i++) {
        var id = due[i].id
        if (!/^[a-z][a-z0-9-]{0,23}$/.test(id) || (id === "openrouter" && root.openrouterLive) || id === "gemini-api") continue
        var gap = Math.max(root.nudgeMin, root.providerFloorMin[id] || 1) * 60000
        // Due once its full gap has passed since the last check by either this
        // widget or Omarchy itself (a few seconds' slack for timing), so Claude
        // is never asked more often than Anthropic allows.
        var last = Math.max(root.lastNudge[id] || 0, (Number(due[i].checkedAt) || 0) * 1000)
        if (now - last < gap - 3000) continue
        root.lastNudge[id] = now
        cmd.push(id)
      }
      if (cmd.length > 1) { nudger.command = cmd; nudger.running = true }
    }
  }

  Timer {
    interval: root.readMs
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  // Usage record id -> the name omarchy-agent knows that agent by. Agents not
  // listed here (gateways, prepaid APIs) have no CLI to start, so a click on
  // them opens the Agents panel instead.
  readonly property var launchers: ({
    claude: "claude", codex: "codex", grok: "grok", copilot: "copilot",
    cursor: "cursor-agent", kimi: "kimi", qwen: "qwen", muse: "muse",
    hermes: "hermes", devin: "devin", agy: "agy", opencode: "opencode"
  })
  // Providers with no app or CLI to start open their own usage page instead.
  readonly property var webPages: ({
    "gemini-api": "https://aistudio.google.com/usage",
    openrouter: "https://openrouter.ai/activity",
    grokbot: "https://cursor.com/dashboard",
    deepseek: "https://platform.deepseek.com/usage"
  })
  function launch(id, launcher) {
    Quickshell.execDetached(["bash", launchScript, launcher, webPages[id] || ""])
  }
  readonly property string launchScript: Qt.resolvedUrl("launch.sh").toString().replace("file://", "")

  function agentTooltip(a) {
    var lines = [a.name + (a.stale ? " (stale)" : "")]
    var p = a.prepaid
    if (p) {
      lines.push("Balance: " + money(p.remaining, p.currency) + (p.funded ? " of " + money(p.funded, p.currency) + " bought" : ""))
      var spent = "Spent today: " + money(p.today, p.currency)
      if (p.todaySince) spent += " (since " + sinceText(p.todaySince) + ")"
      if (p.week !== undefined && p.week !== null) spent += " · week: " + money(p.week, p.currency)
      spent += " · month: " + money(p.month, p.currency)
      if (p.monthSince) spent += " (since " + sinceText(p.monthSince) + ")"
      lines.push(spent)
      lines.push(!p.live ? "From Omarchy's balance, counted locally"
        : p.scope === "account" ? "Live from OpenRouter, all " + (p.keyCount || 0) + " keys (UTC days)"
        : "Live from OpenRouter, this key only (UTC days)")
      var tokens = p.tokens
      if (tokens && [tokens.today, tokens.week, tokens.month].every(
          n => Number.isSafeInteger(n) && n >= 0)) {
        var locale = Qt.locale()
        lines.push("Account tokens (UTC): " + tokens.today.toLocaleString(locale, "f", 0) + " today · "
          + tokens.week.toLocaleString(locale, "f", 0) + " this week · "
          + tokens.month.toLocaleString(locale, "f", 0) + " this month")
      }
      var stats = p.spendStats
      if (stats) {
        lines.push("Account spend (UTC): " + money(stats.today, p.currency) + " today · "
          + money(stats.month, p.currency) + " this month")
        if (typeof stats.dailyAverage === "number")
          lines.push("Average per day (completed days this month): " + money(stats.dailyAverage, p.currency))
        lines.push("Average per month (last 3 completed): " + money(stats.monthlyAverage, p.currency))
      }
    }
    var gm = a.gemini
    if (gm) {
      lines.push("Today: " + gm.requests + " requests" + (gm.errors ? " (" + gm.errors + " failed)" : "")
        + " · " + formatTokens(gm.tokens) + " tokens")
      var names = Object.keys(gm.models || {})
      for (var m = 0; m < names.length && m < 6; m++) {
        var c = gm.models[names[m]]
        lines.push("  " + names[m] + ": " + formatTokens(c["in"]) + " in · " + formatTokens(c.out) + " out")
      }
      var projects = gm.perProject || []
      if (projects.length > 1)
        for (var q = 0; q < projects.length; q++)
          lines.push("  " + projects[q].id + ": " + projects[q].requests + " requests")
      lines.push("Google gives no API for Gemini spend or prepay balance")
    }
    if (a.liveError) lines.push((gm || a.id === "gemini-api" ? "Google Cloud: " : "Live check: ") + a.liveError)
    var x = a.extra
    if (x && x.used !== undefined) {
      var flags = (x.enabled ? "" : " · off") + (x.reached ? " · limit reached" : "") + (x.stale ? " · stale" : "")
      if (extraLeft(x) !== null) {
        lines.push("Extra usage left: " + money(extraLeft(x), x.currency) + " (" + extraLeftPct(x) + "%)" + flags)
        lines.push("Spent this month: " + money(x.used, x.currency) + " of " + money(x.limit, x.currency))
      } else {
        lines.push("Extra usage this month: " + money(x.used, x.currency) + " (no monthly limit)" + flags)
      }
    }
    if (x && x.error) lines.push("Extra usage check: " + x.error)
    if (fullLimits(a).length > 0) {
      var until = blockedUntil(a) ? resetText(blockedUntil(a)) : ""
      lines.push(onExtra(a) ? "Plan limit reached: on extra usage" + (until ? " until " + until : "")
        : "Plan limit reached" + (until ? ": blocked until " + until : ""))
    }
    for (var j = 0; j < a.limits.length; j++) {
      var l = a.limits[j]
      var reset = untilText(l.resetsAt)
      lines.push(l.label + ": " + l.percent.toFixed(0) + "%" + (reset ? "  · resets in " + reset : ""))
    }
    if (lowAlert !== "Off" && limitLow(a)) lines.push("Low: " + remaining(a).toFixed(0) + "% left (alert at " + lowAlertAt + "%)")
    if (lowAlert !== "Off" && balanceLow(a)) lines.push("Low balance (alert at " + money(lowBalanceAt, p.currency) + ")")
    lines.push("")
    lines.push((launchers[a.id] ? "Click: open " + a.name
      : webPages[a.id] ? "Click: " + webPages[a.id].replace(/^https:\/\//, "").split("/")[0] + " usage"
      : "Click: Agents panel") + " · Right: menu · Middle: refresh")
    if (nearLimit(a)) lines.push("Near limit (" + lowAlertAt + "% / balance threshold)")
    return lines.join("\n")
  }

  function formatTokens(n) {
    if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + "B"
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 1 : 2) + "M"
    if (n >= 1e3) return Math.round(n / 1e3) + "K"
    return String(Math.round(n))
  }

  function todayTooltip() {
    var lines = ["Tokens today: " + Number(todayTokens).toLocaleString(Qt.locale(), "f", 0)]
    for (var i = 0; i < todayByAgent.length; i++)
      lines.push("  " + todayByAgent[i].name + ": " + formatTokens(Number(todayByAgent[i].tokens)))
    lines.push("")
    lines.push((barMode === "Full" ? "Click: Agents panel" : "Click: usage list")
      + " · Right: menu · Middle: refresh")
    return lines.join("\n")
  }

  function summaryTooltip() {
    var lines = [alertCount > 0
      ? (alertCount + " provider" + (alertCount === 1 ? "" : "s") + " near a limit")
      : "All shown providers look fine"]
    if (alertCount > 0) lines.push("Tightest remaining: " + worstRemaining.toFixed(0) + "%")
    lines.push("Layout: " + barMode + " · Sort: " + sortOrder)
    lines.push("")
    lines.push("Click: usage list · Right: menu · Middle: refresh")
    return lines.join("\n")
  }

  function openPanel() { Quickshell.execDetached(["omarchy-shell", "omarchy.agents", "toggle"]) }

  property int tick: 0
  Timer {
    interval: 30000
    repeat: true
    running: true
    onTriggered: root.tick++
  }

  Timer {
    id: refreshLater
    interval: 8000
    onTriggered: root.refresh()
  }

  visible: visibleAgents.length > 0 || todayTokens > 0 || summaryMode
  implicitWidth: vertical ? barSize : row.implicitWidth + 4
  implicitHeight: vertical ? row.implicitHeight + 4 : barSize

  Grid {
    id: row
    anchors.centerIn: parent
    flow: root.vertical ? Grid.TopToBottom : Grid.LeftToRight
    columns: root.vertical ? 1 : Math.max(1,
      root.barAgents.length
      + (summaryChip.visible ? 1 : 0)
      + (idleChip.visible ? 1 : 0)
      + (todayItem.visible ? 1 : 0))
    spacing: 0

    // Summary mode: one status chip for the whole strip.
    Item {
      id: summaryChip
      visible: root.summaryMode
      implicitWidth: root.vertical ? root.barSize : summaryLabel.implicitWidth + 10
      implicitHeight: root.vertical ? summaryLabel.implicitHeight + 8 : root.barSize

      Rectangle {
        anchors.fill: parent
        anchors.margins: 2
        radius: Math.min(width, height) / 3
        visible: root.alertCount > 0
        color: Qt.rgba(root.hot.r, root.hot.g, root.hot.b, 0.14)
        border.color: root.hot
        border.width: 1
      }

      Text {
        id: summaryLabel
        anchors.centerIn: parent
        textFormat: Text.PlainText
        text: root.alertCount > 0
          ? (root.alertCount + "|" + root.worstRemaining.toFixed(0) + "%")
          : "OK"
        color: root.alertCount > 0 ? root.hot : root.fg
        font.family: root.font
        font.pixelSize: 11
        font.bold: true
        scale: summaryHit.pressScale
      }

      WidgetButton {
        id: summaryHit
        anchors.fill: parent
        bar: root.bar
        text: ""
        labelVisible: false
        hasVisualContent: true
        tooltipText: (root.tick, root.summaryTooltip())
        onPressed: function(button) { root.barPress(button, null) }
      }
    }

    // Alerts mode with nobody near a limit: a quiet chip so the widget stays findable.
    Item {
      id: idleChip
      visible: root.barMode === "Alerts" && root.barAgents.length === 0 && root.visibleAgents.length > 0
      implicitWidth: root.vertical ? root.barSize : idleLabel.implicitWidth + 10
      implicitHeight: root.vertical ? idleLabel.implicitHeight + 8 : root.barSize

      Text {
        id: idleLabel
        anchors.centerIn: parent
        textFormat: Text.PlainText
        text: "OK"
        color: root.fg
        font.family: root.font
        font.pixelSize: 11
        font.bold: true
        opacity: 0.7
        scale: idleHit.pressScale
      }

      WidgetButton {
        id: idleHit
        anchors.fill: parent
        bar: root.bar
        text: ""
        labelVisible: false
        hasVisualContent: true
        tooltipText: (root.tick, root.summaryTooltip())
        onPressed: function(button) { root.barPress(button, null) }
      }
    }

    Repeater {
      model: root.barAgents

      // Each agent is its own bar button, padded so there are no dead gaps
      // between them. WidgetButton registers with the bar, which is the only
      // way a widget receives clicks and hover tooltips there.
      Item {
        id: agentItem
        required property var modelData
        readonly property var shortWin: root.shortLimit(modelData.limits)
        readonly property var longWin: root.longLimit(modelData.limits)
        implicitWidth: root.vertical ? root.barSize : meters.implicitWidth + (root.iconsOnly ? 6 : 10)
        implicitHeight: root.vertical ? meters.implicitHeight + 8 : root.barSize

        // Low usage ring: a soft tint and outline in the bar's urgent colour.
        Rectangle {
          id: lowRing
          readonly property bool low: root.isLow(agentItem.modelData)
          anchors.fill: parent
          anchors.margins: 2
          anchors.leftMargin: 1
          anchors.rightMargin: 1
          radius: Math.min(width, height) / 3
          visible: low
          color: Qt.rgba(root.hot.r, root.hot.g, root.hot.b, 0.14)
          border.color: root.hot
          border.width: 1.5
          opacity: 1.0

          SequentialAnimation on opacity {
            running: lowRing.low && root.lowAlert === "Pulse"
            loops: Animation.Infinite
            alwaysRunToEnd: true
            NumberAnimation { to: 0.25; duration: 1100; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 1100; easing.type: Easing.InOutSine }
          }
        }

        Row {
          id: meters
          anchors.centerIn: parent
          spacing: 3
          opacity: agentItem.modelData.stale ? 0.55 : 1.0
          scale: hit.pressScale

          // The agent's logo (from Omarchy's own Agents plugin) or its letters.
          // A missing logo falls back to the letters, so an Omarchy update
          // that moves the logos never leaves a blank.
          Item {
            id: badgeBox
            readonly property bool busy: root.activityPulse && root.busyIds.indexOf(agentItem.modelData.id) !== -1
            readonly property bool showLogo: root.useLogos && logo.status === Image.Ready
            anchors.verticalCenter: parent.verticalCenter
            implicitWidth: showLogo ? 14 : letters.implicitWidth
            implicitHeight: Math.max(14, letters.implicitHeight)

            Image {
              id: logo
              anchors.centerIn: parent
              width: 14
              height: 14
              sourceSize.width: 28
              sourceSize.height: 28
              fillMode: Image.PreserveAspectFit
              smooth: true
              asynchronous: true
              visible: badgeBox.showLogo
              source: root.useLogos ? root.logoUrl(agentItem.modelData.id) : ""
            }

            Text {
              id: letters
              anchors.centerIn: parent
              visible: !badgeBox.showLogo
              textFormat: Text.PlainText
              text: root.badge(agentItem.modelData)
              color: root.fg
              font.family: root.font
              font.pixelSize: 11
              font.bold: true
            }

            // A slow breathe while the agent is working.
            SequentialAnimation on opacity {
              running: badgeBox.busy
              loops: Animation.Infinite
              alwaysRunToEnd: true
              NumberAnimation { to: 0.35; duration: 900; easing.type: Easing.InOutSine }
              NumberAnimation { to: 1.0; duration: 900; easing.type: Easing.InOutSine }
            }
          }

          Column {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            visible: !root.iconsOnly && agentItem.modelData.limits.length > 0

            Repeater {
              model: agentItem.modelData.limits.length > 1 ? [agentItem.shortWin, agentItem.longWin]
                : agentItem.modelData.limits.length === 1 ? [agentItem.shortWin] : []

              Rectangle {
                required property var modelData
                width: 18
                height: 3
                radius: 1.5
                color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.25)

                Rectangle {
                  width: Math.max(modelData.percent > 0 ? 1.5 : 0, parent.width * modelData.percent / 100)
                  height: parent.height
                  radius: parent.radius
                  color: modelData.percent >= root.warnAt ? root.hot : root.fg
                }
              }
            }
          }

          // Prepaid balance, or spend today / this month (prepaidText).
          Text {
            anchors.verticalCenter: parent.verticalCenter
            // Prepaid agents show their balance; an agent past its plan limit
            // and running on extra usage shows what that has cost this month.
            readonly property bool extraShown: root.showExtra(agentItem.modelData)
            visible: !root.iconsOnly && (!!agentItem.modelData.prepaid || extraShown || !!agentItem.modelData.gemini)
            textFormat: Text.PlainText
            text: agentItem.modelData.prepaid ? root.prepaidLabel(agentItem.modelData.prepaid)
              : extraShown ? root.extraLabel(agentItem.modelData.extra)
              : agentItem.modelData.gemini ? root.formatTokens(agentItem.modelData.gemini.tokens) : ""
            color: root.balanceLow(agentItem.modelData) || (extraShown && (root.extraSpent(agentItem.modelData)
              || root.extraLow(agentItem.modelData))) ? root.hot : root.fg
            font.family: root.font
            font.pixelSize: 11
            font.bold: true
          }
        }

        WidgetButton {
          id: hit
          anchors.fill: parent
          bar: root.bar
          text: ""
          labelVisible: false
          hasVisualContent: true
          tooltipText: (root.tick, root.agentTooltip(agentItem.modelData))
          onPressed: function(button) { root.barPress(button, agentItem.modelData) }
        }
      }
    }

    // Today's tokens across every agent, the same sum the Agents panel's
    // header line uses.
    Item {
      id: todayItem
      visible: root.showTodayTotal && root.todayTokens > 0
      implicitWidth: root.vertical ? root.barSize : todayLabel.implicitWidth + 10
      implicitHeight: root.vertical ? todayLabel.implicitHeight + 8 : root.barSize

      Text {
        id: todayLabel
        anchors.centerIn: parent
        textFormat: Text.PlainText
        text: "Σ " + root.formatTokens(root.todayTokens)
        color: root.fg
        font.family: root.font
        font.pixelSize: 11
        font.bold: true
        scale: todayHit.pressScale
      }

      WidgetButton {
        id: todayHit
        anchors.fill: parent
        bar: root.bar
        text: ""
        labelVisible: false
        hasVisualContent: true
        tooltipText: (root.tick, root.todayTokens, root.todayTooltip())
        onPressed: function(button) { root.barPress(button, null) }
      }
    }
  }

  // Right-click menu / usage list, built from the same parts as Omarchy's own
  // bar popups. popupKind chooses which column is shown.
  KeyboardPanel {
    id: menu
    anchorItem: root
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: menu.loadedContent
    contentWidth: menu.fittedContentWidth(root.popupKind === "usage" ? Style.space(300) : Style.space(260))
    contentHeight: menu.fittedContentHeight(menu.loadedContentHeight, root.popupKind === "usage" ? Style.space(420) : Style.space(640))

    content: PanelKeyCatcher {
      anchors.fill: parent
      implicitHeight: root.popupKind === "usage" ? usageColumn.implicitHeight : menuColumn.implicitHeight
      onCloseRequested: root.close()

      Column {
        id: menuColumn
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(14)
        visible: root.popupKind === "menu"
        width: parent.width

        MenuChoice {
          title: "Bar layout"
          options: root.barModes
          current: root.barMode
          onChosen: function(value) { root.saveSetting("barMode", value, false) }
        }

        MenuChoice {
          title: "Sort"
          options: root.sortOrders
          current: root.sortOrder
          onChosen: function(value) { root.saveSetting("sortOrder", value, false) }
        }

        MenuChoice {
          title: "Today total (Σ)"
          options: ["On", "Off"]
          current: root.showTodayTotal ? "On" : "Off"
          onChosen: function(value) { root.saveSetting("showTodayTotal", value === "On", true) }
        }

        MenuChoice {
          title: "Agent badge"
          options: ["Letters", "Logos"]
          current: root.useLogos ? "Logos" : "Letters"
          onChosen: function(value) { root.saveSetting("badgeStyle", value, false) }
        }

        MenuChoice {
          title: "Update frequency"
          options: root.frequencies
          current: root.updateFrequency
          onChosen: function(value) { root.saveSetting("updateFrequency", value, false) }
        }

        // Which providers appear in the bar; lit = shown.
        Column {
          width: parent.width
          spacing: Style.space(8)
          visible: root.agents.length > 0

          PanelSectionHeader {
            text: "Show in bar"
            foreground: root.fg
            fontFamily: root.font
          }

          Flow {
            width: parent.width
            spacing: Style.space(6)

            Repeater {
              model: root.agents

              Button {
                required property var modelData
                readonly property bool shown: root.hiddenAgents.indexOf(String(modelData.id)) === -1
                width: (parent.width - Style.space(6) * 3) / 4
                text: root.badge(modelData)
                fontSize: Style.font.bodySmall
                foreground: root.fg
                fontFamily: root.font
                bordered: true
                active: shown
                onClicked: root.setHidden(String(modelData.id), shown)
              }
            }
          }
        }

        Button {
          width: parent.width
          text: "Usage…"
          fontSize: Style.font.bodySmall
          foreground: root.fg
          fontFamily: root.font
          bordered: true
          onClicked: root.openPopup("usage")
        }

        Button {
          width: parent.width
          text: "Settings & providers…"
          fontSize: Style.font.bodySmall
          foreground: root.fg
          fontFamily: root.font
          bordered: true
          onClicked: { root.close(); root.openProviders() }
        }

        Row {
          id: actionRow
          width: parent.width
          spacing: Style.space(6)
          readonly property real cellWidth: (width - spacing) / 2

          Button {
            width: actionRow.cellWidth
            text: "Agents panel"
            fontSize: Style.font.bodySmall
            foreground: root.fg
            fontFamily: root.font
            bordered: true
            onClicked: { root.close(); root.openPanel() }
          }

          Button {
            width: actionRow.cellWidth
            text: "Refresh"
            fontSize: Style.font.bodySmall
            foreground: root.fg
            fontFamily: root.font
            bordered: true
            onClicked: root.refreshNow()
          }
        }
      }

      // Full connected-provider list with meters; click a row to launch.
      Flickable {
        id: usageFlick
        anchors.fill: parent
        visible: root.popupKind === "usage"
        contentWidth: width
        contentHeight: usageColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height

        Column {
          id: usageColumn
          width: usageFlick.width
          spacing: Style.space(10)

          PanelSectionHeader {
            text: "Usage"
            foreground: root.fg
            fontFamily: root.font
          }

          Row {
            width: parent.width
            spacing: Style.space(6)
            readonly property real cellWidth: (width - spacing) / 2

            Button {
              width: parent.cellWidth
              text: "Menu"
              fontSize: Style.font.bodySmall
              foreground: root.fg
              fontFamily: root.font
              bordered: true
              onClicked: root.openPopup("menu")
            }

            Button {
              width: parent.cellWidth
              text: "Refresh"
              fontSize: Style.font.bodySmall
              foreground: root.fg
              fontFamily: root.font
              bordered: true
              onClicked: root.refreshNow()
            }
          }

          Text {
            width: parent.width
            visible: root.usageAgents.length === 0
            textFormat: Text.PlainText
            text: "No providers to show. Sign in via Settings & providers, or unhide them under Show in bar."
            wrapMode: Text.WordWrap
            color: root.fg
            opacity: 0.7
            font.family: root.font
            font.pixelSize: Style.font.bodySmall
          }

          Repeater {
            model: root.usageAgents

            Rectangle {
              id: usageRow
              required property var modelData
              width: usageColumn.width
              height: usageInner.implicitHeight + Style.space(8)
              radius: Style.space(6)
              color: usageClick.containsMouse
                ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.08)
                : (root.nearLimit(modelData) ? Qt.rgba(root.hot.r, root.hot.g, root.hot.b, 0.10) : "transparent")
              border.color: root.nearLimit(modelData) ? root.hot : "transparent"
              border.width: root.nearLimit(modelData) ? 1 : 0

              Row {
                id: usageInner
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Style.space(6)
                anchors.rightMargin: Style.space(6)
                spacing: Style.space(8)

                Item {
                  width: 16
                  height: 16
                  anchors.verticalCenter: parent.verticalCenter
                  readonly property bool showLogo: root.useLogos && usageLogo.status === Image.Ready
                  Image {
                    id: usageLogo
                    anchors.fill: parent
                    visible: parent.showLogo
                    source: root.useLogos ? root.logoUrl(usageRow.modelData.id) : ""
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                  }
                  Text {
                    anchors.centerIn: parent
                    visible: !parent.showLogo
                    textFormat: Text.PlainText
                    text: root.badge(usageRow.modelData)
                    color: root.fg
                    font.family: root.font
                    font.pixelSize: 11
                    font.bold: true
                  }
                }

                Column {
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: 2
                  width: Math.min(72, usageInner.width * 0.28)

                  Text {
                    textFormat: Text.PlainText
                    text: usageRow.modelData.name
                    elide: Text.ElideRight
                    width: parent.width
                    color: root.fg
                    font.family: root.font
                    font.pixelSize: Style.font.bodySmall
                    font.bold: true
                  }

                  Text {
                    textFormat: Text.PlainText
                    text: {
                      var a = usageRow.modelData
                      if (a.prepaid) return root.prepaidLabel(a.prepaid)
                      if (root.showExtra(a)) return root.extraLabel(a.extra)
                      if (a.gemini) return root.formatTokens(a.gemini.tokens)
                      if (a.limits.length > 0) return root.remaining(a).toFixed(0) + "% left"
                      return a.stale ? "stale" : ""
                    }
                    color: root.nearLimit(usageRow.modelData) ? root.hot : root.fg
                    opacity: 0.85
                    font.family: root.font
                    font.pixelSize: 10
                  }
                }

                Column {
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: 2
                  visible: usageRow.modelData.limits.length > 0

                  Repeater {
                    model: {
                      var limits = usageRow.modelData.limits
                      if (limits.length > 1) return [root.shortLimit(limits), root.longLimit(limits)]
                      if (limits.length === 1) return [root.shortLimit(limits)]
                      return []
                    }

                    Rectangle {
                      required property var modelData
                      width: 64
                      height: 4
                      radius: 2
                      color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.22)

                      Rectangle {
                        width: Math.max(modelData.percent > 0 ? 2 : 0, parent.width * modelData.percent / 100)
                        height: parent.height
                        radius: parent.radius
                        color: modelData.percent >= root.warnAt ? root.hot : root.fg
                      }
                    }
                  }
                }
              }

              MouseArea {
                id: usageClick
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
                cursorShape: Qt.PointingHandCursor
                onClicked: function(mouse) {
                  if (mouse.button === Qt.RightButton) root.openPopup("menu")
                  else if (mouse.button === Qt.MiddleButton) root.refreshNow()
                  else { root.close(); root.activateAgent(usageRow.modelData) }
                }
              }
            }
          }
        }
      }
    }
  }

  // A titled row of mutually exclusive buttons; the current one is lit.
  component MenuChoice: Column {
    id: choice
    property string title: ""
    property var options: []
    property string current: ""
    signal chosen(string value)

    width: parent ? parent.width : 0
    spacing: Style.space(8)

    PanelSectionHeader {
      text: choice.title
      foreground: root.fg
      fontFamily: root.font
    }

    Row {
      id: choiceRow
      width: parent.width
      spacing: Style.space(6)
      readonly property real cellWidth: choice.options.length > 0
        ? (width - spacing * (choice.options.length - 1)) / choice.options.length : 0

      Repeater {
        model: choice.options

        Button {
          required property var modelData
          width: choiceRow.cellWidth
          text: String(modelData)
          fontSize: Style.font.bodySmall
          foreground: root.fg
          fontFamily: root.font
          bordered: true
          active: choice.current === modelData
          onClicked: choice.chosen(String(modelData))
        }
      }
    }
  }
}
