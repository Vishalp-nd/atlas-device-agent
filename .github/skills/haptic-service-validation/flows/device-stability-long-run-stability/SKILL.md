---
name: device-stability-long-run-stability
description: "Use when: documenting long-run haptic_feedback stability beyond the discrete reboot scenarios. This flow is tracked in confluence and is currently in progress rather than fully evaluated."
argument-hint: "device ID (e.g., /device-stability-long-run-stability 440073)"
---

# Haptic — Flow 17: Device Stability - Long Run Stability

## What happens

This flow represents extended long-run stability beyond the discrete reboot and kill
scenarios. The source bucket states that confluence tracks this as in progress, with no
final pass or fail verdict yet.

**When active:** Long-duration manual or field stability review
**Frequency:** Extended-duration validation only
**Cross-service impact:** broad system stability
**Automated:** No
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence long-run stability note | long-run stability is tracked but not yet fully evaluated |

## Pass criteria

- Not applicable yet; the source material marks this flow as in progress

## Fail signals

- Not applicable yet; no final verdict exists in the source material

## Validation instructions

1. Report this flow as not yet automated
2. Report the current status as in progress rather than pass or fail
3. Do not overstate coverage for this flow