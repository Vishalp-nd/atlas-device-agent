---
name: post-ignition-analytics-seconds
description: "Use when: validating haptic behavior during the post-ignition analytics window. Covers alignment with the configured post_ignition_analytics_seconds value."
argument-hint: "device ID (e.g., /post-ignition-analytics-seconds 440073)"
---

# Haptic — Flow 33: Post Ignition Analytics Seconds

## What happens

This flow validates that haptic behavior during the post-ignition analytics window
follows the configured `post_ignition_analytics_seconds` value.

**When active:** Analytics timing validation
**Frequency:** Per timing-focused scenario
**Cross-service impact:** analytics timing and haptic trigger gating
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | haptic behavior follows `post_ignition_analytics_seconds` |

## Pass criteria

- Observed haptic behavior aligns with the configured post-ignition analytics window

## Fail signals

- Haptic behavior falls outside the expected post-ignition analytics window

## Validation instructions

1. Report this flow as not yet automated
2. Use config and timing evidence together when assessing this flow