---
name: circbuff-critical-errors
description: "Use when analyzing Circular Buffer critical events, specifically CODE=20000 (Cloud notify failed / SM_E_CB_CREATION_FAIL)."
---

# Circular Buffer Critical Errors

Apply the shared narrowing method and confidence rubric in `critical-event-query-triage/SKILL.md` to every tuple below.

## `SM_E_CB_CREATION_FAIL` (CODE=20000)

**Identify:** PROCESS=`CB`. This code covers two distinct emitter branches — disambiguate by `DESCRIPTION` pattern, since `CODE_AUX` semantics differ between them:

| DESCRIPTION pattern | Branch | CODE_AUX meaning | Confidence |
|---|---|---|---|
| `Cloud notify failed, response code %` | cloud-notify curl path | libcurl return code (`res`) | `High` — code path and tuple confirmed on 2 devices (see caveat on claimed cause below) |
| `Failed to create_table_db%` | DB table creation init path | usually unused sentinel | `Low` — exists in source, but **zero occurrences found across all 11 devices' logs in this repo**; every observed `create_table_db` log line says "success", never "Failed" |

**In plain terms:** The device tried to tell the cloud about a new recorded clip, but the upload/notify request failed. This is usually a symptom of the device having just rebooted — its network (Wi-Fi/modem/GPS) is still reconnecting, so the first few cloud-notify attempts fail until the connection is fully back up. It can also keep recurring every few minutes during a longer outage unrelated to any specific reboot. (The DB-table-creation variant of this same code exists in the source code but was never actually observed firing in any of the device logs checked.)

**Why it triggered:** Source: `circular_buffer/src/circular_buffer.cpp`. Cloud-notify branch is emitted after `curl_easy_perform(...)` returns a non-OK result while posting the video-list payload; DB-creation branch (unconfirmed in logs) would be emitted when table-creation helpers return false during Circular Buffer startup. Checked against raw `service_mon.log` on both cited devices, the previously-claimed "network-instability precursor" story (`98002`/`98005`/`98009`/`37501` reliably precede every occurrence within about a minute) does **not** hold up as a general rule. It's only loosely true for the *first* `20000` right after a fresh boot/reconnect (and even then the gap runs closer to ~2 minutes, with only some of those codes present) — it's false for the large majority of repeated `20000` occurrences: sessions run for hours with dozens of `20000` failures and no fresh precursor code anywhere nearby. The named "precursor" codes are themselves just routine modem/GPS re-init telemetry (`98002` = modem firmware version string, `98009` = APN name, `37501` = GPS port enumerate time) logged once whenever the modem/GPS restarts — not an instability signal by themselves. `30008` (NDC alert-replay failure) does recur roughly 1:1 with `20000` during sustained outages, but that's because both independently retry on their own ~300s cadence during the same outage, not because one causes the other.

**Evidence** (log-validated, cross-device) in `service_mon.log`, checked on devices `103062502288` and `103062502308`:

Tuple: `PROCESS=CB, CODE=20000, CODE_AUX=<curl code>, DESCRIPTION="Cloud notify failed, response code <n>"`

- On `103062502308`, a 12.6-hour session had 161 occurrences of `CODE=20000` but only 14-24 occurrences each of the claimed precursor codes (`98002`/`98005`/`98009`/`37501`) — they cluster once per hour or less at reconnect/boot, while `20000` fires every ~2-5 minutes throughout. Most `20000` firings have no precursor anywhere near them.
- On `103062502288`, the same shape: precursor-family codes cluster only at the start of each reconnect/boot session, then go silent for hours while `20000` keeps firing roughly every 300s.
- The one relationship that genuinely holds up: `103062502288` also fires `CODE=30023` ("Camera LPM crash" — see `ndcentral-critical-errors`) twice, and both times a device reboot (per `reboot.log`) starts ~11-37s *before* the crash, fresh modem/GPS-init telemetry appears ~19-20s after, and the first post-reboot `CODE=20000` fires ~97-152s after the crash. All three — `30023`, the modem/GPS-init codes, and `20000` — are independent downstream siblings of the *same reboot event*, not a chain where one causes the next.

**Confidence:** `High` that the tuple/emitter identification for the cloud-notify branch is correct. `Low` on any claimed network-instability-precursor correlation — checked against real logs and holds only loosely for the very first post-reboot occurrence, not as a general rule for repeated failures. `Low`/unconfirmed on the DB-creation branch entirely — no supporting log line found in any of the 11 devices sampled.
