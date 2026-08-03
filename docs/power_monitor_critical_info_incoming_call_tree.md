# Incoming Call Tree for shutdownReasonContent (power_monitor)

## Scope

- Function: shutdownReasonContent(..., power_monitor_shutdown_reason reason, ...)
- Definition: power_monitor/src/power_monitor.cpp:2668

## Mermaid Flow

```mermaid
flowchart TD
  A[power_monitor event handlers] --> B[initiate_shutdown]
  B --> C[shutdownReasonContent]

  C --> D1[SHUTDOWN_FOR_BAD_VOLTAGE]
  C --> D2[SHUTDOWN_FOR_IGNITION_OFF]
  C --> D3[SHUTDOWN_FOR_SVC_REBOOT]
  C --> D4[other shutdown reasons]

  D1 --> E1[send_err_msg SM_E_PM_BAD_VOLTAGE_SHDN]
  D2 --> E2[send_err_msg SM_E_PM_IGNITION_OFF_SHUTDOWN]
  D3 --> E3[send_err_msg SM_E_PM_SVC_SHUTDOWN]
  D4 --> E4[mapped PM shutdown codes]
```

## Deterministic Text Call Tree

### Target function

- power_monitor/src/power_monitor.cpp:2668
  - void shutdownReasonContent(...)

### Direct caller

- power_monitor/src/power_monitor.cpp:2863
  - bool initiate_shutdown(int shutdown_after_secs, power_monitor_shutdown_reason reason)
- power_monitor/src/power_monitor.cpp:2913
  - `initiate_shutdown` calls `shutdownReasonContent(...)`

### Representative upstream callers of `initiate_shutdown`

- power_monitor/src/power_monitor.cpp:3334
  - low-power wakeup path
- power_monitor/src/power_monitor.cpp:3975
  - bad-battery shutdown path
- power_monitor/src/power_monitor.cpp:4023
  - SVC reboot request path
- power_monitor/src/power_monitor.cpp:4155
  - AWSIOT-triggered shutdown path

### Sink examples inside target

- power_monitor/src/power_monitor.cpp:2685
  - `SM_E_PM_BAD_VOLTAGE_SHDN`
- power_monitor/src/power_monitor.cpp:2705
  - `SM_E_PM_IGNITION_OFF_SHUTDOWN`
- power_monitor/src/power_monitor.cpp:2765
  - `SM_E_PM_SVC_SHUTDOWN`
- power_monitor/src/power_monitor.cpp:2841
  - `SM_E_PM_UNKNOWN_INIT_SHDN`
