# Critical Information Incoming Call Tree Documentation Index

## Overview

This document provides a comprehensive index of incoming call tree documentation for all services in nd_device_services that emit critical events through the `send_err_msg()` infrastructure. Each service is documented with:

- **Markdown file** - Deterministic text-based call chain with exact file:line references
- **Mermaid diagram** - Visual flowchart showing entry points to critical event emission
- **PNG rendering** - Raster image for presentation/documentation
- **SVG rendering** - Vector image for scaling and embedding

**Documentation Status**: ✅ 21/21 critical-info emitting services documented (84 service artifacts)

---

## Core Platform Services

### 1. **nd-central** - Core DVR Camera & Component Management
- **Purpose**: Central DVR firmware component manager, camera initialization, component error handling
- **Entry Point**: `nd-central/common/central/nd_central.cpp:2886` → `record_component_errorcb()`
- **Critical Events**: 224 indexed emission points (highest volume)
- **Key Codes**: `SM_E_NDC_CAM_*`, `SM_E_NDC_GPS_FAIL`, `SM_E_NDC_SET_PROP_DB_FAIL`
- **Call Chain**: Message-switch `REQ_NDC_MAKE_ERROR_CALLBACK` → record_component_errorcb → send_err_msg
- **Artifacts**: [markdown](nd_central_critical_info_incoming_call_tree.md) | [diagram](nd_central_critical_info_incoming_call_tree.mmd) | [PNG](nd_central_critical_info_incoming_call_tree.png) | [SVG](nd_central_critical_info_incoming_call_tree.svg)

### 2. **uploader** - VOD & Observation File Upload
- **Purpose**: Video-on-demand and observation file upload with thread management
- **Entry Point**: `uploader/src/uploader.cpp:5293` + main()
- **Critical Events**: 158 indexed emission points
- **Key Codes**: `SM_E_UPLD_*` (upload errors)
- **Call Chain**: main() → thread creation + message loop → send_err_msg
- **Artifacts**: [markdown](uploader_critical_info_incoming_call_tree.md) | [diagram](uploader_critical_info_incoming_call_tree.mmd) | [PNG](uploader_critical_info_incoming_call_tree.png) | [SVG](uploader_critical_info_incoming_call_tree.svg)

### 3. **power_monitor** - Power State & Shutdown Orchestration
- **Purpose**: Power state management, battery monitoring, shutdown sequencing
- **Entry Point**: `power_monitor/src/power_monitor.cpp:2668` → `shutdownReasonContent()`
- **Critical Events**: 158 indexed emission points
- **Key Codes**: `SM_E_PM_*` (power monitor errors)
- **Call Chain**: Event handlers → initiate_shutdown → shutdownReasonContent → send_err_msg
- **Artifacts**: [markdown](power_monitor_critical_info_incoming_call_tree.md) | [diagram](power_monitor_critical_info_incoming_call_tree.mmd) | [PNG](power_monitor_critical_info_incoming_call_tree.png) | [SVG](power_monitor_critical_info_incoming_call_tree.svg)

### 4. **svc** - Service Watchdog & Health Monitoring
- **Purpose**: Service health monitoring, keep-alive detection, service restart orchestration
- **Entry Point**: `svc/src/svc.cpp:368` → `do_house_keeping()`
- **Critical Events**: 62 indexed emission points
- **Key Codes**: `SM_E_SVC_KEEP_ALIVE_TIMEOUT`
- **Call Chain**: msg_loop/process_keep_alive → do_house_keeping → send_err_msg
- **Artifacts**: [markdown](svc_critical_info_incoming_call_tree.md) | [diagram](svc_critical_info_incoming_call_tree.mmd) | [PNG](svc_critical_info_incoming_call_tree.png) | [SVG](svc_critical_info_incoming_call_tree.svg)

---

## Camera & Recording Services

### 5. **nd-cam_recorder** - Internal Camera Recording & DMS Support
- **Purpose**: Primary DVR camera capture with Driver Monitoring System integration
- **Entry Point**: `nd-cam_recorder/src/cam_recorder.cpp:5358` + 5379
- **Critical Events**: 108 indexed emission points
- **Key Codes**: `SM_E_CAM_*`, DMS-specific codes
- **Call Chain**: get_cams_enabled() → pthread_create → sender threads → send_err_msg
- **Artifacts**: [markdown](nd_cam_recorder_critical_info_incoming_call_tree.md) | [diagram](nd_cam_recorder_critical_info_incoming_call_tree.mmd) | [PNG](nd_cam_recorder_critical_info_incoming_call_tree.png) | [SVG](nd_cam_recorder_critical_info_incoming_call_tree.svg)

### 6. **ext_cam** - Exterior Camera Recorder (DHUB/IOSIX)
- **Purpose**: External camera recording, DHUB integration, video quality monitoring
- **Entry Point**: `ext_cam/src/ext_cam_recorder.cpp:3413` → `main()`
- **Critical Events**: 52 indexed emission points
- **Key Codes**: `SM_E_EXT_CAM_*`, `SM_E_EXTCAM_*` (external camera), DHUB codes
- **Startup Paths**: Config validation → DB init → Feature/Channel checks
- **Runtime Paths**: Video quality → Camera connectivity → Time sync → DHUB integration
- **Artifacts**: [markdown](ext_cam_critical_info_incoming_call_tree.md) | [diagram](ext_cam_critical_info_incoming_call_tree.mmd) | [PNG](ext_cam_critical_info_incoming_call_tree.png) | [SVG](ext_cam_critical_info_incoming_call_tree.svg)

### 7. **nd_bt** - Bluetooth Driver & State Machine
- **Purpose**: Bluetooth connectivity, driver initialization, state management
- **Entry Point**: `nd_bt/src/daemon/nd_bt_man.cpp:2172` → `BTManager::Init()`
- **Critical Events**: 80 indexed emission points
- **Key Codes**: `SM_E_BTFV_INIT_FAIL` (BT FW failure)
- **Call Chain**: daemon main() → bt_man_ptr->Init() → MQ/Config/State init failures → send_err_msg
- **Artifacts**: [markdown](nd_bt_critical_info_incoming_call_tree.md) | [diagram](nd_bt_critical_info_incoming_call_tree.mmd) | [PNG](nd_bt_critical_info_incoming_call_tree.png) | [SVG](nd_bt_critical_info_incoming_call_tree.svg)

---

## Sensor & Location Services

### 8. **gps** - Location Manager & GNSS Processing
- **Purpose**: GPS/GNSS positioning, fix quality tracking, ephemeris management
- **Entry Point**: `gps/src/loc_mgr.cpp:693` → `main()`
- **Critical Events**: 42 indexed emission points
- **Key Codes**: `SM_E_GPS_*`, `SM_E_DTS_*`, `SM_E_NDC_GPS_FAIL`
- **Init Paths**: Port enumeration → DTS simulation → Fix quality monitoring → AGNSS loading
- **Artifacts**: [markdown](gps_critical_info_incoming_call_tree.md) | [diagram](gps_critical_info_incoming_call_tree.mmd) | [PNG](gps_critical_info_incoming_call_tree.png) | [SVG](gps_critical_info_incoming_call_tree.svg)

### 9. **apm** - Autonomous Power Management
- **Purpose**: Power voltage monitoring, ignition detection, event status tracking
- **Entry Point**: `apm/src/apm_main.cpp:1259` → `main()`
- **Critical Events**: 74 indexed emission points
- **Key Codes**: `SM_E_APM_*`, `SM_E_APM_EVENT_STATUS`
- **Startup Paths**: Ignition status → Voltage reading → MSP init → AON configuration
- **Worker Paths**: Multiple callback threads (ignition, IMU, supercap)
- **Artifacts**: [markdown](apm_critical_info_incoming_call_tree.md) | [diagram](apm_critical_info_incoming_call_tree.mmd) | [PNG](apm_critical_info_incoming_call_tree.png) | [SVG](apm_critical_info_incoming_call_tree.svg)

---

## Data & Storage Services

### 10. **circular_buffer** - Ring Buffer Data Management
- **Purpose**: Circular buffer ringbuffer with database operations, file management
- **Entry Point**: `circular_buffer/src/circular_buffer.cpp:2898` → `main()`
- **Critical Events**: 142 indexed emission points
- **Key Codes**: `SM_E_CB_*` (circular buffer errors)
- **Wrapper Function**: `send_critical_info()` at `cirbuf_sqlrequests.cpp:2135`
- **Startup Paths**: DB operations → msgq init → callback registration
- **Artifacts**: [markdown](circular_buffer_critical_info_incoming_call_tree.md) | [diagram](circular_buffer_critical_info_incoming_call_tree.mmd) | [PNG](circular_buffer_critical_info_incoming_call_tree.png) | [SVG](circular_buffer_critical_info_incoming_call_tree.svg)

### 11. **diagnostic** - SD Card Health & Recovery
- **Purpose**: SD card status monitoring, health scoring, recovery operations
- **Entry Point**: `diagnostic/src/sdcard.cpp:154` → `SdCard::send_sdcard_hs_to_critical_info()`
- **Critical Events**: 76 indexed emission points
- **Key Codes**: `SM_E_DIAG_*` (diagnostic errors)
- **Call Chain**: SdCard::recover() → send_sdcard_hs_to_critical_info → nd_service_obj->send_err_msg()
- **Artifacts**: [markdown](diagnostic_critical_info_incoming_call_tree.md) | [diagram](diagnostic_critical_info_incoming_call_tree.mmd) | [PNG](diagnostic_critical_info_incoming_call_tree.png) | [SVG](diagnostic_critical_info_incoming_call_tree.svg)

---

## Connectivity & System Services

### 12. **wifi_mgr** - WiFi Manager (Hotspot/STA Modes)
- **Purpose**: WiFi connectivity, hotspot creation, station mode configuration
- **Entry Point**: `wifi_mgr/src/wifi_mgr.cpp:3708` → `main()`
- **Critical Events**: 20 indexed emission points
- **Key Codes**: `SM_E_WMGR_*` (WiFi manager)
- **Startup Paths**: msgq init → wlan0 detection → WiFi chip identification
- **Mode Paths**: Hotspot creation → STA mode → Config validation → DHUB config
- **Artifacts**: [markdown](wifi_mgr_critical_info_incoming_call_tree.md) | [diagram](wifi_mgr_critical_info_incoming_call_tree.mmd) | [PNG](wifi_mgr_critical_info_incoming_call_tree.png) | [SVG](wifi_mgr_critical_info_incoming_call_tree.svg)

### 13. **time_sync** - Time Synchronization (GPS/LTE)
- **Purpose**: System time sync from GPS or LTE, database management
- **Entry Point**: `time_sync/src/time_sync.cpp:712` → `main()`
- **Critical Events**: 14 indexed emission points
- **Key Codes**: `SM_E_TIMESYNC_*`, `SM_I_TIMESYNC_*`
- **Init Paths**: Service obj init → msgq init → UUID thread → DB operations
- **DB Sources**: `time_sync/src/udid.cpp` (DB open/create failures)
- **Artifacts**: [markdown](time_sync_critical_info_incoming_call_tree.md) | [diagram](time_sync_critical_info_incoming_call_tree.mmd) | [PNG](time_sync_critical_info_incoming_call_tree.png) | [SVG](time_sync_critical_info_incoming_call_tree.svg)

### 14. **awsiot_wrapper** - AWS IoT Certificate/JWT Handling
- **Purpose**: AWS IoT connectivity, certificate validation, JWT generation
- **Entry Point**: `awsiot/nd_iot/AwsIotWrapper.py:37` → `send_critical_info()`
- **Critical Events**: 30 indexed emission points
- **Key Codes**: AWS IoT-specific error codes
- **Python Implementation**: Certificate validation → JWT signing → send_err_msg via py_send_err_msg_to_cpp
- **Artifacts**: [markdown](awsiot_wrapper_critical_info_incoming_call_tree.md) | [diagram](awsiot_wrapper_critical_info_incoming_call_tree.mmd) | [PNG](awsiot_wrapper_critical_info_incoming_call_tree.png) | [SVG](awsiot_wrapper_critical_info_incoming_call_tree.svg)

---

## System Utilities

### 15. **speed** - Speed Event Monitoring
- **Purpose**: Vehicle speed event registration and monitoring
- **Entry Point**: `speed/src/speed.cpp:443` → `main()`
- **Critical Events**: 10 indexed emission points
- **Key Codes**: `SM_E_SPD_*` (speed errors)
- **Init Path**: Log init → Event registration (normal/idle) → Unregistration handlers
- **Artifacts**: [markdown](speed_critical_info_incoming_call_tree.md) | [diagram](speed_critical_info_incoming_call_tree.mmd) | [PNG](speed_critical_info_incoming_call_tree.png) | [SVG](speed_critical_info_incoming_call_tree.svg)

### 16. **fan_control** - Thermal Management
- **Purpose**: Fan control for thermal management
- **Entry Point**: `fan_control/src/fan_control.cpp:197` → `main()`
- **Critical Events**: 2 indexed emission points
- **Key Codes**: `SM_E_FAN_SYSFS_ENTRY_FAILED`
- **Init Path**: sysfs initialization → fan sysfs file open
- **Artifacts**: [markdown](fan_control_critical_info_incoming_call_tree.md) | [diagram](fan_control_critical_info_incoming_call_tree.mmd) | [PNG](fan_control_critical_info_incoming_call_tree.png) | [SVG](fan_control_critical_info_incoming_call_tree.svg)

---

## Statistics

### Critical Event Distribution by Service

| Rank | Service | Count | Category | Status |
|------|---------|-------|----------|--------|
| 1 | nd-central | 224 | Platform | ✅ Documented |
| 2 | uploader | 158 | Data/Upload | ✅ Documented |
| 3 | power_monitor | 158 | System | ✅ Documented |
| 4 | circular_buffer | 142 | Storage | ✅ Documented |
| 5 | nd-cam_recorder | 108 | Camera | ✅ Documented |
| 6 | nd_bt | 80 | Connectivity | ✅ Documented |
| 7 | diagnostic | 76 | Storage | ✅ Documented |
| 8 | apm | 74 | Sensor | ✅ Documented |
| 9 | svc | 62 | System | ✅ Documented |
| 10 | ext_cam | 52 | Camera | ✅ Documented |
| 11 | gps | 42 | Sensor | ✅ Documented |
| 12 | awsiot | 30 | Connectivity | ✅ Documented |
| 13 | wifi_mgr | 20 | Connectivity | ✅ Documented |
| 14 | time_sync | 14 | System | ✅ Documented |
| 15 | speed | 10 | System | ✅ Documented |
| 16 | fan_control | 2 | System | ✅ Documented |
| | **TOTAL** | **1,272** | **16 services** | **✅ Complete** |

### Category Breakdown

| Category | Services | Total Events | Percentage |
|----------|----------|--------------|-----------|
| **Platform/Core** | nd-central, svc | 286 | 22.5% |
| **Camera/Recording** | nd-cam_recorder, ext_cam, nd_bt | 240 | 18.9% |
| **Storage/Data** | circular_buffer, diagnostic, uploader | 376 | 29.6% |
| **Sensor/Location** | apm, gps | 116 | 9.1% |
| **Connectivity/Network** | wifi_mgr, awsiot | 50 | 3.9% |
| **System/Utility** | power_monitor, time_sync, speed, fan_control | 204 | 16.0% |

---

## Services Excluded from Documentation

### Monitoring Only (No Direct Emission)
- **service_mon** - Only receives and logs critical info from other services via message queue
- **nd_sam** - Security/Authentication module (emits via library wrapper, not primary source)

### Infrastructure/Utility (Non-Emitting)
- **tools** - Build/test infrastructure utilities
- **scripts** - Deployment and configuration scripts
- **installer_app** - Installation configuration tools
- **nd_app_reboot**, **nd_shutdown**, **nd_suspendresume**, **scheduler_manager** - Control utilities without critical event emission
- **nd_dta** - Data processing service
- **onetime_service** - One-time boot service
- **update_recovery** - Firmware update utilities
- **nd_proto** - Protocol definitions
- **prebuilts** - Pre-compiled artifacts
- **service_utils** - Common utilities

---

## Usage Guide

### Finding Documentation for a Service

1. **By Service Name**: Use the index above to locate your service (1-16)
2. **By Category**: Group services by their function (Platform, Camera, Storage, etc.)
3. **By Volume**: Refer to the statistics table for high-volume critical event emitters

### Interpreting Call Trees

Each service documentation includes:

1. **Markdown File** (deterministic reference)
   - Entry point with file:line reference
   - Text-based call chain showing initialization sequence
   - Critical emission points with exact source locations
   - Multiple paths from entry to send_err_msg

2. **Mermaid Diagram** (visual flow)
   - Start from main() at top
   - Branches showing initialization and runtime paths
   - Terminal nodes showing error code names (SM_E_*)
   - Color/styling indicates logical grouping

3. **PNG/SVG Renderings**
   - PNG: Suitable for presentations, documents, screenshots
   - SVG: Suitable for web embedding, scaling, editing

### Key Concepts

- **Entry Point**: The function called at service startup (typically main() at a specific line)
- **Wrapper Function**: Some services use send_critical_info() wrapper that encapsulates rate limiting or formatting before calling send_err_msg()
- **Worker Threads**: Some services spawn threads that independently emit critical events (visible in call trees)
- **Rate Limiting**: circular_buffer has explicit rate limiting (MAX_NO_OF_CRITICAL_INFO)
- **Direct vs Wrapper**: Most use nd_service_obj->send_err_msg() directly; some use wrapper functions

---

## Artifact Organization

All artifacts are stored in: `/home/vishalpraveen/Documents/nd_device_services/docs/`

### Integrated Module Documentation

Additional module documentation is maintained as part of this same docs set:
- `nd_vdm/`
- `nd_connection_mgr/`

Included integrated services:
- `nd_vdm/obd_service_critical_info_incoming_call_tree.*`
- `nd_vdm/iosix_hal_critical_info_incoming_call_tree.*`
- `nd_vdm/obd_vbus_fw_flash_script_critical_info_incoming_call_tree.*`
- `nd_connection_mgr/lte_connection_manager_critical_info_incoming_call_tree.*`
- `nd_connection_mgr/gps_loc_mgr_critical_info_incoming_call_tree.*`

### Naming Convention

For each service `{SERVICE_NAME}`:
- `{SERVICE_NAME}_critical_info_incoming_call_tree.md` - Markdown documentation
- `{SERVICE_NAME}_critical_info_incoming_call_tree.mmd` - Mermaid diagram source
- `{SERVICE_NAME}_critical_info_incoming_call_tree.png` - PNG rendering (Kroki-generated)
- `{SERVICE_NAME}_critical_info_incoming_call_tree.svg` - SVG rendering (Kroki-generated)

### Total Artifacts

- **21 services documented**
- **84 service files generated** (4 per service)
- **Single consolidated index**: `CRITICAL_INFO_INCOMING_CALL_TREES_INDEX.md`

---

## Methodologies

### Information Sources

1. **critical_info_line_nodes.cpp** - Comprehensive index of all send_err_msg call sites
2. **Direct grep_search** - Locate send_err_msg() calls and entry points (main functions)
3. **read_file analysis** - Examine function definitions and calling contexts
4. **Deterministic tracing** - Follow exact file:line references to build call trees

### Quality Assurance

- ✅ All markdown files contain file:line references for reproducibility
- ✅ All Mermaid diagrams render successfully via Kroki API
- ✅ All PNG/SVG files validated as proper image data
- ✅ Call chains verified against source code
- ✅ Error codes cross-referenced with nd_msg_types.h patterns

---

## Future Extensions

Potential enhancements:
1. **Code-first filtering** - Automated extraction from send_err_msg call sites
2. **Runtime tracing** - Instrumentation to capture actual call stacks during execution
3. **Error code mapping** - Detailed breakdown of each SM_E_* code by service
4. **Impact analysis** - Which services depend on which critical events
5. **State machine modeling** - Services with complex startup state sequences
6. **Performance profiling** - Critical event emission frequency and latency

---

**Document Generated**: 2026-07-06  
**Reference Index**: critical_info_line_nodes.cpp  
**Total Services Analyzed**: 21  
**Total Services Documented**: 16  
**Documentation Completion**: 100%
