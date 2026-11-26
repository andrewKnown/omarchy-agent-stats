import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// One small meter pair per agent subscription: a letter, then the short
// (5-hour) window on top and the longest window below. It only reads the
// records omarchy-agent-usage-update writes (via read.py). Click an agent to
// open it in a terminal (launch.sh), right click for this widget's menu
// (badge style, pulse, low usage alert, Agents panel, refresh), middle click to refresh.
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
  property var busyIds: []
  readonly property bool useLogos: String(setting("badgeStyle", "Letters")) === "Logos"
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
  // On extra usage the plan limit is already spent, so "low" means the extra
  // usage credit left this month, by the same % threshold.
  function isLow(agent) {
    if (lowAlert === "Off") return false
    if (balanceLow(agent)) return true
    if (onExtra(agent) && extraLeftPct(agent.extra) !== null) return extraLow(agent)
    return limitLow(agent)
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
    if (extraText === "Left" && extraLeft(x) !== null)
      return money(extraLeft(x), x.currency) + " " + extraLeftPct(x) + "%"
    return money(x.used, x.currency)
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
    return "file://" + logoDir + id + (lightBar && lightLogos[id] ? "-light" : "") + ".svg"
  }
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
    running: root.activityPulse && root.agents.length > 0
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
  readonly property var badges: ({ claude: "CL", codex: "CX", grok: "GK", cursor: "CU", copilot: "CP" })
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
      if (root.updateEveryMin > 0) cmd.push("--every=" + root.updateEveryMin * 60)
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

  // Best-effort faster updates: Omarchy's Agents plugin refreshes the usage
  // records on its own schedule (every 15 minutes by default). With
  // updateEveryMin set, this widget also asks Omarchy's own updater for fresh
  // limits (--limits-only, the cheap kind the Agents panel runs when opened)
  // for just the agents shown. Never more often than every 3 minutes, the
  // pace Omarchy itself keeps to, because Anthropic refuses faster probes.
  readonly property int updateEveryMin: {
    var n = Math.round(Number(setting("updateEveryMin", 0)) || 0)
    return n <= 0 ? 0 : Math.max(3, Math.min(60, n))
  }
  Process {
    id: nudger
    onExited: root.refresh()
  }
  Timer {
    interval: Math.max(3, root.updateEveryMin) * 60000
    repeat: true
    running: root.updateEveryMin > 0
    onTriggered: {
      if (nudger.running) return
      var cmd = ["omarchy-agent-usage-update", "--limits-only"]
      for (var i = 0; i < root.agents.length; i++) {
        var id = String(root.agents[i].id)
        if (/^[a-z][a-z0-9-]{0,23}$/.test(id) && !(id === "openrouter" && root.openrouterLive)) cmd.push(id)
      }
      if (cmd.length > 2) { nudger.command = cmd; nudger.running = true }
    }
  }

  Timer {
    interval: root.intervalMs
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
    if (a.liveError) lines.push("Live check: " + a.liveError)
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
    lines.push((launchers[a.id] ? "Click: open " + a.name : "Click: Agents panel") + " · Right: menu · Middle: refresh")
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
    lines.push("Click: Agents panel · Right: menu · Middle: refresh")
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

  visible: agents.length > 0 || todayTokens > 0
  implicitWidth: vertical ? barSize : row.implicitWidth + 4
  implicitHeight: vertical ? row.implicitHeight + 4 : barSize

  Grid {
    id: row
    anchors.centerIn: parent
    flow: root.vertical ? Grid.TopToBottom : Grid.LeftToRight
    columns: root.vertical ? 1 : Math.max(1, root.agents.length + (todayItem.visible ? 1 : 0))
    spacing: 0

    Repeater {
      model: root.agents

      // Each agent is its own bar button, padded so there are no dead gaps
      // between them. WidgetButton registers with the bar, which is the only
      // way a widget receives clicks and hover tooltips there.
      Item {
        id: agentItem
        required property var modelData
        readonly property var shortWin: root.shortLimit(modelData.limits)
        readonly property var longWin: root.longLimit(modelData.limits)
        implicitWidth: root.vertical ? root.barSize : meters.implicitWidth + 10
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
            visible: agentItem.modelData.limits.length > 0

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
            readonly property bool extraShown: root.onExtra(agentItem.modelData)
            visible: !!agentItem.modelData.prepaid || extraShown
            textFormat: Text.PlainText
            text: agentItem.modelData.prepaid ? root.prepaidLabel(agentItem.modelData.prepaid)
              : extraShown ? root.extraLabel(agentItem.modelData.extra) : ""
            color: root.balanceLow(agentItem.modelData) || (extraShown && root.extraLow(agentItem.modelData)) ? root.hot : root.fg
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
          onPressed: function(button) {
            var launcher = root.launchers[agentItem.modelData.id]
            if (button === Qt.MiddleButton) {
              Quickshell.execDetached(["omarchy-shell", "omarchy.agents", "refresh"])
              refreshLater.restart()
            } else if (button === Qt.RightButton) {
              root.toggle()
            } else if (!launcher) {
              root.openPanel()
            } else {
              Quickshell.execDetached(["bash", root.launchScript, launcher])
            }
          }
        }
      }
    }

    // Today's tokens across every agent, the same sum the Agents panel's
    // header line uses.
    Item {
      id: todayItem
      visible: root.todayTokens > 0
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
        onPressed: function(button) {
          if (button === Qt.MiddleButton) {
            Quickshell.execDetached(["omarchy-shell", "omarchy.agents", "refresh"])
            refreshLater.restart()
          } else if (button === Qt.RightButton) {
            root.toggle()
          } else {
            root.openPanel()
          }
        }
      }
    }
  }

  // Right-click menu, built from the same parts as Omarchy's own bar popups.
  KeyboardPanel {
    id: menu
    anchorItem: root
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: menu.loadedContent
    contentWidth: menu.fittedContentWidth(Style.space(240))
    contentHeight: menu.fittedContentHeight(menu.loadedContentHeight)

    content: PanelKeyCatcher {
      anchors.fill: parent
      implicitHeight: menuColumn.implicitHeight
      onCloseRequested: root.close()

      Column {
        id: menuColumn
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(14)

        MenuChoice {
          title: "Agent badge"
          options: ["Letters", "Logos"]
          current: root.useLogos ? "Logos" : "Letters"
          onChosen: function(value) { root.saveSetting("badgeStyle", value, false) }
        }

        MenuChoice {
          title: "Pulse while working"
          options: ["Off", "On"]
          current: root.activityPulse ? "On" : "Off"
          onChosen: function(value) { root.saveSetting("activityPulse", value === "On" ? "true" : "false", true) }
        }

        MenuChoice {
          title: "Low usage alert"
          options: ["Off", "Ring", "Pulse"]
          current: root.lowAlert
          onChosen: function(value) { root.saveSetting("lowAlert", value, false) }
        }

        MenuChoice {
          title: "Alert at % remaining"
          options: ["5", "10", "20", "30"]
          current: String(root.lowAlertAt)
          onChosen: function(value) { root.saveSetting("lowAlertAt", value, true) }
        }

        MenuChoice {
          visible: root.hasPrepaid || root.openrouterLive
          title: "Prepaid shows"
          options: ["Balance", "Today", "Month"]
          current: root.prepaidText
          onChosen: function(value) { root.saveSetting("prepaidText", value, false) }
        }

        MenuChoice {
          visible: root.hasPrepaid || root.openrouterLive
          title: "Low balance at"
          options: ["Off", "$1", "$5", "$10", "$20"]
          current: root.lowBalanceAt > 0 ? "$" + root.lowBalanceAt : "Off"
          onChosen: function(value) { root.saveSetting("lowBalanceAt", value === "Off" ? "0" : value.slice(1), true) }
        }

        MenuChoice {
          visible: root.hasPrepaid || root.openrouterLive
          title: "OpenRouter usage source"
          options: ["Local estimates", "OpenRouter API"]
          current: root.openrouterLive ? "OpenRouter API" : "Local estimates"
          onChosen: function(value) {
            root.saveSetting("openrouterLive", value === "OpenRouter API" ? "true" : "false", true)
          }
        }

        MenuChoice {
          title: "Claude extra usage"
          options: ["Off", "On"]
          current: root.claudeExtra ? "On" : "Off"
          onChosen: function(value) { root.saveSetting("claudeExtra", value === "On" ? "true" : "false", true) }
        }

        MenuChoice {
          visible: root.claudeExtra
          title: "Extra usage shows"
          options: ["Left", "Spent"]
          current: root.extraText
          onChosen: function(value) { root.saveSetting("extraText", value, false) }
        }

        MenuChoice {
          title: "Update usage every"
          options: ["Off", "3m", "5m", "10m", "15m"]
          current: root.updateEveryMin > 0 ? root.updateEveryMin + "m" : "Off"
          onChosen: function(value) { root.saveSetting("updateEveryMin", value === "Off" ? "0" : parseInt(value), true) }
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
            onClicked: {
              Quickshell.execDetached(["omarchy-shell", "omarchy.agents", "refresh"])
              refreshLater.restart()
              root.refresh()
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
