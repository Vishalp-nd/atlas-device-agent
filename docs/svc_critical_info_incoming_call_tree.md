# Incoming Call Tree for do_house_keeping (svc)

## Scope

- Function: do_house_keeping()
- Definition: svc/src/svc.cpp:368

## Mermaid Flow

```mermaid
flowchart TD
  A[msg_loop periodic path] --> B[do_house_keeping]
  C[process_keep_alive self message] --> B

  B --> D1[CRITICAL service timeout]
  B --> D2[NON_CRITICAL service timeout]

  D1 --> E1[send_err_msg SM_E_SVC_KEEP_ALIVE_TIMEOUT]
  D1 --> F[do_power_mon_or_system_reboot]
  F --> E2[send_err_msg SM_E_PM_POR_GPIO_FAIL]

  D2 --> E3[send_err_msg SM_E_SVC_KEEP_ALIVE_TIMEOUT]
```

## Deterministic Text Call Tree

### Target function

- svc/src/svc.cpp:368
  - static void do_house_keeping()

### Direct callers

- svc/src/svc.cpp:509
  - `process_keep_alive()` self keepalive message path
- svc/src/svc.cpp:818
  - `msg_loop()` periodic fallback path when svc utils/msgq init fails

### Critical sink paths

- svc/src/svc.cpp:400
  - CRITICAL service timeout -> `SM_E_SVC_KEEP_ALIVE_TIMEOUT`
- svc/src/svc.cpp:406
  - NON_CRITICAL service timeout -> `SM_E_SVC_KEEP_ALIVE_TIMEOUT`
- svc/src/svc.cpp:360
  - reboot fallback path emits `SM_E_PM_POR_GPIO_FAIL`
