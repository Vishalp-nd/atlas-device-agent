---
name: <service-name>-service-validation
description: "Use when: validating <ServiceDisplayName> (<process_name>) service behavior from device logs. Covers <comma-separated list of flows, e.g., initialization, keepalive, watchdog, config recovery, disk monitoring>."
argument-hint: "device ID (e.g., /<service-name>-service-validation 440073)"
---

# <ServiceDisplayName> (`<process_name>`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads the pytest `.py` test cases for actual log patterns,
> device-type paths, and assertions — this skill does NOT duplicate those.

---

## Service Overview

<!-- 3-5 sentences from service source code explaining the service's role in the
     system. Focus on WHY the service exists and its responsibilities. -->

`<process_name>` is a <critical/background/crontab-managed> service that <primary responsibility>.
It handles <responsibility 1>, <responsibility 2>, and <responsibility 3>.
The service interacts with <other services> via <mechanism, e.g., message queue, IPC, file system>.

**Process name:** `<process_name>`
**Log file:** `<svc>.log` (resolved per device type by the `device` fixture / test code)
**Primary config sections:** `[<section1>]`, `[<section2>]`

---

## Service Flows

<!-- Each flow is a distinct behavior the service performs. Derived from source
     code analysis. This is the core of the skill — it maps the conceptual
     "what does this service do?" to concrete test case coverage. -->

### Flow 1: <Flow Name> (e.g., Watchdog Initialization)

**What happens:** <2-3 sentences explaining the flow from source code — what triggers
it, what steps it performs internally, what the expected outcome is>

**When active:** Always / Only when `[<section>] <key>` is enabled
**Frequency:** Once at boot / Every Ns / On event
**Cross-service impact:** <Does this flow affect or depend on other services?>

**Test cases that validate this flow:**
| Test Case ID     | What it checks                          |
| ---------------- | --------------------------------------- |
| `TC_<svc>_NNN`   | <brief: e.g., "init pattern appears">   |
| `TC_<svc>_NNN`   | <brief>                                 |

---

### Flow 2: <Flow Name>

**What happens:** ...

**When active:** ...
**Frequency:** ...
**Cross-service impact:** ...

**Test cases that validate this flow:**
| Test Case ID     | What it checks                          |
| ---------------- | --------------------------------------- |
| `TC_<svc>_NNN`   | <brief>                                 |

---

<!-- Repeat for each flow -->

### Flow N: <Flow Name>

**What happens:** ...

**When active:** ...
**Frequency:** ...
**Cross-service impact:** ...

**Test cases that validate this flow:**
| Test Case ID     | What it checks                          |
| ---------------- | --------------------------------------- |
| `TC_<svc>_NNN`   | <brief>                                 |

---

## Config-Driven Flow Activation

<!-- This is the key section that tells the agent which test cases to run
     based on the device's actual config. The agent reads config from:
       device_data/device_<ID>_config.ini  (fetched by fetch-device-config skill)
       device_data/device_list_config.csv  (CSV with config columns)
     
     Map config keys to flows so the agent can skip test cases for flows
     that are not active on a given device. -->

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section | Config Key        | Value       | Activates Flow(s)              | Test Cases Affected                  |
| -------------- | ----------------- | ----------- | ------------------------------ | ------------------------------------ |
| `[<section>]`  | `<key>`           | `true` / `1`| <Flow Name>                    | `TC_<svc>_NNN`, `TC_<svc>_NNN`      |
| `[<section>]`  | `<key>`           | `<value>`   | <Flow Name> (with threshold N) | `TC_<svc>_NNN`                       |
| —              | —                 | —           | <Flow Name> (always active)    | `TC_<svc>_NNN`, `TC_<svc>_NNN`      |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- Flows gated by a config key → run only if the device config has that key set to the activating value
- If the config key is missing from the device config → use the default value listed above
- Config values in `device_list_config.csv` take precedence if present (they reflect live production config)

---

## Cross-Service Dependencies

<!-- Which other service logs should the agent check alongside this service's
     logs. Helps the agent correlate events across log files. -->

| Related Service    | Why                                                        | When to check its logs                  |
| ------------------ | ---------------------------------------------------------- | --------------------------------------- |
| `<service_name>`   | <e.g., "sends keepalive messages TO this service">         | When validating <Flow Name>             |
| `<service_name>`   | <e.g., "receives cleanup trigger FROM this service">       | When validating <Flow Name>             |

---

## Flow Dependency Graph

<!-- Optional: shows which flows depend on other flows or external events.
     Helps the agent understand ordering and prerequisites. -->

```
boot → [Flow: Init] → [Flow: Watchdog Start] → periodic kicks
                    → [Flow: Recovery Thread] → sanity checks every Ns
                    → [Flow: Disk Monitor] → free space checks every Ns
event (service crash) → [Flow: Keepalive Timeout] → reboot
config change → [Flow: Config-Driven Feature] (only if enabled)
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine active flows** using the Config-Driven Flow Activation table above
3. **For each active flow**, read the mapped pytest `.py` test files from `tests/<svc>/`
4. **From each `.py` test**, use its assertions / `device.search_log(...)` patterns for log patterns and the device-type paths resolved in the test code
5. **Search device logs** in `device_logs/<device_id>/` using patterns from the test
6. **For cross-service checks**, also search logs of related services listed above
7. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
