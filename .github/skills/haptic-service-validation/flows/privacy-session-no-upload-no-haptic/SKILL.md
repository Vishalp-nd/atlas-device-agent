---
name: privacy-session-no-upload-no-haptic
description: "Use when: validating privacy-mode alert-session behavior. Covers the expectation that no data upload occurs and no haptic trigger occurs while privacy mode is active."
argument-hint: "device ID (e.g., /privacy-session-no-upload-no-haptic 440073)"
---

# Haptic — Flow 31: Privacy Session (No Upload, No Haptic)

## What happens

When the device is in privacy mode, the source material states that there should be no
data upload and no haptic trigger.

**When active:** Privacy-mode alert-session validation
**Frequency:** Per privacy-mode scenario
**Cross-service impact:** privacy mode, upload path, and haptic trigger path
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | no upload and no haptic in privacy mode |

## Pass criteria

- No upload occurs while privacy mode is active
- No haptic trigger occurs while privacy mode is active

## Fail signals

- Upload occurs in privacy mode
- Haptic triggers in privacy mode

## Validation instructions

1. Report this flow as not yet automated
2. Keep this runtime privacy behavior separate from the metadata-only privacy-session flow