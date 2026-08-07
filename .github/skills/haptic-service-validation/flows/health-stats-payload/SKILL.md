---
name: health-stats-payload
description: "Use when: validating haptic-related health stats payload behavior as its own atomic flow. Covers payload content and reporting expectations."
argument-hint: "device ID (e.g., /health-stats-payload 440073)"
---

# Haptic — Flow 50: Health Stats Payload

## What happens

This flow validates haptic-related health stats payload behavior as its own target:
payload content and reporting expectations.

**When active:** Health payload validation
**Frequency:** As needed for payload review
**Cross-service impact:** health payload generation and reporting
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| Existing health-stats-payload bucket skill | haptic-related health payload content |

## Pass criteria

- Health payload contains the expected haptic-related reporting fields

## Fail signals

- Health payload omits or misreports haptic-related fields

## Validation instructions

1. Use this flow when the question is about payload reporting rather than runtime trigger behavior
2. Keep it separate from observation-payload-haptic-status if the payload type differs