---
name: accessory-detection
description: "Use when: validating haptic accessory detection as its own atomic flow. Covers whether the service correctly recognizes the presence or absence of the haptic accessory path."
argument-hint: "device ID (e.g., /accessory-detection 440073)"
---

# Haptic — Flow 51: Accessory Detection

## What happens

This flow validates haptic accessory detection as its own target: whether the service
correctly recognizes the presence or absence of the haptic accessory path.

**When active:** Accessory-state validation
**Frequency:** As needed for accessory troubleshooting
**Cross-service impact:** accessory state and haptic availability
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| Existing accessory-detection bucket skill | accessory presence/absence detection for haptic path |

## Pass criteria

- Accessory presence or absence is detected correctly

## Fail signals

- Accessory state is misdetected, leading to wrong haptic availability assumptions

## Validation instructions

1. Use this flow when the question is about accessory-state detection rather than trigger behavior
2. Keep it separate from runtime motor-disconnection flows