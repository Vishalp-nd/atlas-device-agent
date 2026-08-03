# Incoming Call Tree for create_ext_vod_thread and Startup Critical Emitters (uploader)

## Scope

Primary target:

- uploader/src/uploader.cpp:5293
  - bool create_ext_vod_thread()

Supporting startup emitter context:

- uploader/src/uploader.cpp:5339
  - int main()

## Mermaid Flow

```mermaid
flowchart TD
  A[main] --> B1[create_ext_vod_thread at boot]
  A --> B2[start_receive_cb_broadcast_thread]

  C[message loop PAYLOAD_LARGE] --> D1[cancelled external VOD]
  C --> D2[new external VOD]

  D1 --> B1
  D2 --> B1

  B1 --> E1[send_err_msg SM_E_UPLD_MUTEX_INIT_FAIL]
  B1 --> E2[send_err_msg SM_E_UPLD_THREAD_CREATE_FAIL]

  A --> F[startup init failures]
  F --> G1[SM_E_UPLD_LOG_INIT_FAIL]
  F --> G2[SM_E_UPLD_CFG_PARSE_FAIL]
  F --> G3[SM_E_UPLD_DB_CREAT_FAIL]
```

## Deterministic Text Call Tree

### Target function

- uploader/src/uploader.cpp:5293
  - bool create_ext_vod_thread()

### Direct callers

- uploader/src/uploader.cpp:5625
  - `main()` bootup path when pending external VOD requests exist
- uploader/src/uploader.cpp:5977
  - message loop `PAYLOAD_LARGE` path for cancelled external VOD
- uploader/src/uploader.cpp:6037
  - message loop `PAYLOAD_LARGE` path for new external VOD

### Sink inside target

- uploader/src/uploader.cpp:5299
  - mutex init fail -> `SM_E_UPLD_MUTEX_INIT_FAIL`
- uploader/src/uploader.cpp:5309
  - ext VOD thread create fail -> `SM_E_UPLD_THREAD_CREATE_FAIL`

### Additional startup emitter anchor

- uploader/src/uploader.cpp:5339
  - `main()` emits critical startup errors directly (log init, config parse, DB create, msgq/thread create)
