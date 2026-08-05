---
name: <process-or-family>-critical-errors
description: "Use when analyzing <Family> critical events, specifically CODE=<code1> (<enum_label1>)[, CODE=<code2> (<enum_label2>), ...]."
---

# <Family> Critical Errors

<!--
Format contract for the `*-critical-errors` skill family. Every section must
serve one purpose: helping the agent explain WHY a tuple fired. State every
fact once. Do NOT restate the shared narrowing method/confidence rubric from
`critical-event-query-triage/SKILL.md` — point to it instead of copying it.
Do NOT add a "Source" or "Verify on device" section: source is a citation,
fold it inline into "Why it triggered"; on-device verification is a human
follow-up action, not part of identifying why something already fired.

MANDATORY before writing "Why it triggered" or "Evidence": grep the actual
raw device logs (device_logs/<device_id>/*.log) for every claimed causal or
correlation relationship — do not trust the error enum name, code comment,
or `send_err_msg(...)` label as the root cause, and do not assume two codes
that land near each other in time are causally related. Every skill in this
family that skipped this step turned out to have at least one unsupported
claim once checked:
  - ndcentral: assumed "LPM" in the name meant an actual sleep/wake cycle —
    logs showed the crash instead correlates with device reboot + ignition
    transition, no LPM marker anywhere nearby.
  - circbuff: assumed nearby modem/GPS codes meant "network was already
    flaky" — logs showed those are routine per-boot init telemetry that
    only coincidentally clusters near the failure, true for the first
    post-reboot occurrence but false for the vast majority of repeats; the
    DB-creation branch had zero supporting log lines across 11 devices.
  - obd: the escalation-to-camera-crash claim for CODE=95001 was not just
    unsupported, one instance showed the *reverse* causality (camera crash
    triggers reboot, not CAN failure triggering camera crash).
  - btfv: assumed one device's failure was a "single occurrence" and that
    a single log signature applied everywhere — logs showed it was actually
    the higher-frequency device (2000+ occurrences), and the proximate
    failure signature (exit-status-1 vs. a timeout+D-Bus-error pattern) was
    device-specific, not universal.
If a claimed correlation/causal story does NOT hold up under a real grep,
say so explicitly in the skill and downgrade confidence accordingly — do
not silently drop the claim or present it as fact anyway. State plainly
what IS confirmed vs. what was assumed from naming/co-occurrence.
Confidence can (and often should) be split per-claim within one code block,
e.g. "High that the tuple identification is correct. Low on the claimed
correlation to <other code> — checked against real logs and found to be
coincidental clustering, not causation."
-->

Apply the shared narrowing method and confidence rubric in `critical-event-query-triage/SKILL.md` to every tuple below.

## `<ENUM_LABEL>` (CODE=<code>)

**Identify:** PROCESS=`<x>`, DESCRIPTION matches `<pattern>`, `CODE_AUX` = <meaning>.

<!--
If this CODE has multiple description/aux variants that need disambiguating
(see circbuff-critical-errors for CODE=20000's two branches), add a small
table here instead of a separate contract section:

| DESCRIPTION pattern | Branch | CODE_AUX meaning | Confidence |
|---|---|---|---|
| `<pattern 1>` | <branch 1> | <aux meaning 1> | <High/Medium/Low> |
| `<pattern 2>` | <branch 2> | <aux meaning 2> | <High/Medium/Low> |
-->

**In plain terms:** <1-2 sentences, no jargon, no code/file names — describe WHEN this
tends to happen (what the device/vehicle/user was doing) and HOW it breaks, in language
a non-engineer would understand. This is the answer a user actually wants; everything
below is the technical backing for it. If a plausible-sounding mechanism (e.g. from the
enum name) turned out NOT to hold up against real logs, describe what's actually
confirmed instead, not the assumed story.>

**Why it triggered:** <one tight technical explanation merging mechanism and precondition — cite the source inline as a parenthetical or trailing sentence, e.g. "Source: `<repo/path/to/file.cpp>`, `send_err_msg(<ENUM_LABEL>, <aux_expr>, \"<description>\")`.". Do not give source its own heading. Explicitly separate what the code/enum name/comment says this is from what raw device logs actually confirm — if they diverge, say so here.>

**Evidence** (log-validated[, cross-device, deterministic]) in `<logfile>`, checked on device(s) `<id1>`, `<id2>`:

Tuple: `PROCESS=<x>, CODE=<n>, CODE_AUX=<n>, DESCRIPTION="<...>"`

1. <log line / event>
2. <log line / event>

<Key distinguishing observation, if any — including any claimed correlation/causal
relationship that did NOT hold up under a real grep, stated plainly as "this does not
hold up" rather than omitted.>

**Confidence:** `<High/Medium/Low>` — <one-line rationale per distinct claim, e.g. "High
that the tuple identification is correct. Low on the claimed correlation to <code> —
checked against real logs and found to be coincidental clustering, not causation.">

<!-- Repeat this `##` block per code covered by this skill -->
