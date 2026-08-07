---
name: logging-operations
description: "Use when: validating haptic logging behavior as its own atomic flow. Covers log presence, expected log patterns, and log usefulness for downstream validation."
argument-hint: "device ID (e.g., /logging-operations 440073)"
---

# Haptic — Flow 48: Logging Operations

## What happens

This flow treats haptic logging behavior as its own validation target: log presence,
expected log patterns, and whether logs are sufficient for downstream validation.

**When active:** Log-focused validation
**Frequency:** As needed during troubleshooting or evidence review
**Cross-service impact:** none beyond evidence collection
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| Existing logging-operations bucket skill | log presence and expected haptic log patterns |

## Pass criteria

- Required haptic logs are present and usable for validation

## Fail signals

- Required haptic logs are missing or insufficient for validation

## Validation instructions

1. Use this flow when the question is about evidence quality rather than runtime behavior
2. Keep it separate from service-lifecycle and trigger-path flows