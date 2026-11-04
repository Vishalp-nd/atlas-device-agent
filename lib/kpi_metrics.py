"""
KPI Summary for logs: per boot / device / OTA numbers for the KPI tables of the OTA report.

Layout of this file (read top to bottom to verify a KPI):
  1. KPI_LOG_LINES  every log line a KPI reads: service log, grep literal, regex, a verbatim
                    sample line (with the device-day it was taken from).
  2. KPI_TABLES     the report's KPI tables; each metric names the lines it uses and how its
                    value is computed from them (the compute functions follow in section 3).
  3. Engine         scan_day() greps a device-day (lib/scanners/kpi.py, so the day is read
                    once with every other scanner); Device + compute() turn a device's hits
                    into per-boot samples; summarize() / merge_summaries() roll them up.

Conventions
  * Log lines are `<epoch_ms>: <service process uptime_ms>: TAG: LVL: pid: tid: message`
    (Python services: `YYYY-MM-DD HH:MM:SS,mmm` UTC); log_search.line_timestamp reads both.
  * Power-on anchor of a boot (t=0 of the "since power on" metrics) = epoch - system uptime
    from the first uptime line in the boot: syslog_monitor `uptime N s`, else ndcentral
    `System Uptime: N seconds`, else power_mon `check_uptime ... uptime_secs N`. The
    report's own boot start (earliest service start) is 8-30 s later than this. No boot LED
    state is logged; the LED hints of the KPI list map to the nearest logged event.
  * Camera index: 0 outward, 1 inward, 2 left, 3 right (`Bagheera3 Left cam 2`), 4-7 MDVR
    (Octo: 4, 5, 6), 8 DMS. The lines are the same for every camera, only the number
    changes, so the per-camera KPIs are made for each camera of CAMS; the columns of
    cameras 4-8 (OPTIONAL_CAMS) are shown only when some device logged them.
  * "Since power on" metrics are computed per power-on: the anchors of all uptime lines, the
    ones within POWER_ON_CLUSTER_MS of each other being one power-on (this does not depend on
    the report's boot detection, which can merge a short hung boot with the next one). Each
    value is then attributed to the report boot its line falls in, a boot starting at its
    power-on (lines logged before the services start, e.g. @Reboot at 12 s, belong to it).
  * Times are seconds unless the metric says otherwise; downtime lines start before the
    power-on and end after it, the value goes to the boot of the end line.
  * Count metrics are 0 (not empty) in the boots whose logs could show the line: every boot
    for lines also in critical logs, else the boots with detailed logs.
  * Most lines are I-level: devices that upload only critical (_c) logs get only the
    metrics whose line is marked crit=True.
  * Metrics with no log line (status NOT_FOUND below) are kept in the tables, empty, so the
    gap is visible.
"""

import bisect
import re
import subprocess
from collections import Counter, defaultdict, namedtuple

# ════════════════════════════════════════════════════════════════════════════════════════
# 1. KPI_LOG_LINES: key -> Line(service, grep, regex, sample, crit)
#    service: the log file(s) (<service>.log / <service>_c.log; 'syslogs' = syslogs/*.log)
#    grep:    plain text every matching line contains (the grep prefilter); a tuple = any of
#    regex:   Python regex run on the line, named groups are what the metrics use
#    crit:    True if the line is also in the critical-only (_c) logs
# ════════════════════════════════════════════════════════════════════════════════════════

Line = namedtuple('Line', 'service grep regex sample crit')
Line.__new__.__defaults__ = (False,)

KPI_LOG_LINES = {
    # ── Power-on anchor / boot ───────────────────────────────────────────────────────────
    'syslog_uptime': Line(
        'syslogs', 'uptime ', r'SYSLOG_MON: I: \d+: \d+: uptime (?P<s>\d+\.\d+) s < 60 s',
        '1793148914428: 2061: SYSLOG_MON: I: 6030: 6030: uptime 26.4 s < 60 s: starting background scan from start of file (270335 bytes)  [103102600722_2026-10-28/syslogs/log_1793148913598.log]'),
    'ndc_system_uptime': Line(
        'ndcentral', 'System Uptime: ', r'NDC: I: \d+: \d+: System Uptime: (?P<s>\d+) seconds',
        '1793148911737: 206: NDC: I: 6110: 6110: System Uptime: 23 seconds  [103102600722_2026-10-28/ndcentral.log]'),
    'pwr_uptime': Line(
        'power_mon', 'check_uptime max_uptime', r'check_uptime max_uptime \d+ uptime_secs (?P<s>[1-5]?\d)$',
        '1793143047604: 6940451: PWR: I: 5922: 6755: check_uptime max_uptime 172800 uptime_secs 6964  [103102600722_2026-10-28/power_mon.log] (only the first minute, < 60 s, is kept: anchor)'),
    'reboot_entering': Line(
        'reboot', '@Reboot Entering', r'@Reboot Entering',
        '2026-10-28 00:55:02,578 - reboot - INFO - ::====================::@Reboot Entering::====================::  [103102600722_2026-10-28/reboot.log]'),
    'ndc_service_uptime': Line(
        'ndcentral', 'serviceUpTime: ', r'NDC: I: \d+: \d+: serviceUpTime: (?P<s>\d+)$',
        '1793148927548: 16017: NDC: I: 6110: 6110: serviceUpTime: 16  [103102600722_2026-10-28/ndcentral.log]'),
    'gps_enum': Line(
        'gps', 'GPS Port Enumerate Time', r'GPSD: I: \d+: \d+: GPS Port Enumerate Time: (?P<s>\d+) sec$',
        '1793148919561: 7538: GPSD: I: 5975: 6266: GPS Port Enumerate Time: 31 sec  [103102600722_2026-10-28/gps.log]'),
    'sm_gps_enum': Line(
        'service_mon', 'GPS Port Enumerate Time', r'ND_GPS : \d+ : \d+ : -?\d+ : GPS Port Enumerate Time: \d+ seconds : (?P<ms>\d+)',
        '1793148919562: 7849: SM: E: 6001: 6001: Service error: ND_GPS : 1793148919561 : 37501 : 0 : GPS Port Enumerate Time: 31 seconds : 31562  [103102600722_2026-10-28/service_mon.log]', True),
    'svc_start': Line(
        'svc', 'Starting SVC', r'######Starting SVC######',
        '1793148910115: 331: SVC: I: 5924: 5924: ######Starting SVC######  [103102600722_2026-10-28/svc.log]'),
    'analytics_start': Line(
        'analytics', 'Starting Analytics Service', r'######## Starting Analytics Service',
        '1793148910904: 200: AS: I: 5911: 5911: ######## Starting Analytics Service ######  [103102600722_2026-10-28/analytics.log]'),
    'zmq_socket': Line(
        'cam_rec', 'SUCCESS IN CREATE zmq_socket', r'CAM_REC: I: \d+: \d+: SUCCESS IN CREATE zmq_socket',
        '1793148925369: 7991: CAM_REC: I: 6440: 6440: SUCCESS IN CREATE zmq_socket  [103102600722_2026-10-28/cam_rec.log]'),
    'first_frame': Line(
        'ndcentral', 'first frame for cam num 0', r'first frame for cam num 0 registered@ (?P<epoch>\d{13})',
        '1793148927351: 15820: NDC: I: 6110: 6920: first frame for cam num 0 registered@ 1793148927351  [103102600722_2026-10-28/ndcentral.log]'),
    'button_ready': Line(
        'svc', 'Button event thread created successfully', r'Button event thread created successfully',
        '1793684837920: 1156: SVC: I: 3673: 3673: Button event thread created successfully  [264080392_2026-11-03/svc.log]'),
    'lte_session': Line(
        'conn_mgr', 'LTE Data Session started successfully', r'CONN_MGR_SESSION: I: \d+: \d+: LTE Data Session started successfully',
        '1793148969342: 6563: CONN_MGR_SESSION: I: 8001: 8001: LTE Data Session started successfully  [103102600722_2026-10-28/conn_mgr.log]'),
    'ka_reached': Line(
        'power_mon', 'reached_cloud 1', r'response_string :.*: reached_cloud 1',
        '1793148973182: 61191: PWR: I: 6010: 6802: response_string :{"response":true,"msg":"upload-logs"}: reached_cloud 1  [103102600722_2026-10-28/power_mon.log]'),
    'gps_ttff': Line(
        'gps', 'GPS First Fix time', r'GPS First Fix time: (?P<s>\d+) sec',
        '1793155724563: 15871: GPSD: I: 5879: 6019: GPS First Fix time: 37 sec  [103102600722_2026-10-28/gps.log]'),
    'prev_shutdown': Line(
        'power_mon', 'previous_shutdown_reason', r'POWER_MONITOR_ctx->previous_shutdown_reason (?P<reason>\S+)',
        '1793155718957: 9032: PWR: I: 5880: 5880: POWER_MONITOR_ctx->previous_shutdown_reason DBSTATE_SHUTDOWN_MISC_LOWPOWER:SHUTDOWN  [103102600722_2026-10-28/power_mon.log]'),
    'wakeup_reason': Line(
        ('power_mon', 'apm'), 'WAKEUP_REASON:', r'DEV\w*: I: \d+: \d+: WAKEUP_REASON:\s*(?P<wakeup>.+?)\s*--\s*RESET_REASON:\s*(?P<reset>\S+)',
        '1793239013133: 10281: DEVB3: I: 5973: 5973: WAKEUP_REASON::POWER_UP -- RESET_REASON::POWER_DOWN:TEGRA_BCCPLEX_WATCHDOG  [log_prod/103102600722/2026-10-29/power_mon]'),
    'pass_high': Line(
        'power_mon', 'Uptime Check Pass HIGH', r'CYCLIC_REBOOT: Uptime Check Pass HIGH',
        '1793566422289: 172763949: PWR: C: 3757: 4125: CYCLIC_REBOOT: Uptime Check Pass HIGH  [log_prod/6603078588/2026-11-01/power_mon]', True),
    'uptime_long': Line(
        'power_mon', 'check_uptime max_uptime', r'check_uptime max_uptime (?P<max>\d+) uptime_secs (?P<s>[1-9]\d{5,})$',
        '1793568222631: 174564291: PWR: I: 3757: 4125: check_uptime max_uptime 172800 uptime_secs 174609  [log_prod/6603078588/2026-11-01/power_mon] (only uptimes >= 100000 s are kept)'),
    'soak_done': Line(
        'power_mon', 'CYCLIC_REBOOT: Soak done', r'CYCLIC_REBOOT: Soak done (?P<soak>\d+)s, rebooting',
        '1793568222635: 174564295: PWR: C: 3757: 4125: CYCLIC_REBOOT: Soak done 1800s, rebooting  [log_prod/6603078588/2026-11-01/power_mon]', True),
    'shutdown_reason': Line(
        'power_mon', 'shutdown reason ::@ time',
        r'shutdown reason ::@ time (?P<start>\d{13})\s+(?P<mode>Shuttingdown|restarting) because of (?P<reason>[A-Z_]+)',
        '1793144189003: 8081850: PWR: C: 5922: 5922: shutdown reason ::@ time 1793144189001\tShuttingdown because of SHUTDOWN_FOR_IGNITION_OFF \t::  [103102600722_2026-10-28/power_mon.log]', True),
    'shutdown_initiated': Line(
        'power_mon', 'Device Shutdown Initiated', r'Device Shutdown Initiated \(defer_req_cnt=',
        '1793144910620: 8803467: PWR: I: 5922: 6756: Device Shutdown Initiated (defer_req_cnt=0, defer_time_applied=0s, defer_consumed=NO)  [103102600722_2026-10-28/power_mon.log]'),
    'shutdown_exec': Line(
        'power_mon', 'system_execute cmd: ', r'SYS_U: I: \d+: \d+: system_execute cmd: (?P<cmd>shutdown now|poweroff|reboot)\b',
        '1793144916027: 8808874: SYS_U: I: 5922: 6636: system_execute cmd: shutdown now  [103102600722_2026-10-28/power_mon.log]'),
    'pm_start': Line(
        'power_mon', 'STARTING POWER MONITOR', r'#####STARTING POWER MONITOR#####',
        '1793239003547: 695: PWR: E: 5973: 5973: #####STARTING POWER MONITOR#####  [log_prod/103102600722/2026-10-29/power_mon]', True),

    # ── OTA & firmware ───────────────────────────────────────────────────────────────────
    'ota_size': Line(
        ('otacheck', 'updater'), 'OTA package size in bytes', r'OTA package size in bytes = (?P<bytes>\d+)',
        '2026-10-27 12:54:55,365 - __main__ - INFO - OTA package size in bytes = 45930165, OTA required size in KB = 491643.201171875, space left in device in KB = 10765052  [log_prod/6603014391/2026-10-27/otacheck]'),
    'sm_update_time': Line(
        'service_mon', 'updateTime: ', r'updateEngine : \d{13} : 210003 : -?\d+ : (?P<version>\S+) \| updateTime: (?P<s>[\d.]+)',
        '1793107036850: 1710: SM: E: 9632: 9632: Service error: updateEngine : 1793107036849 : 210003 : -1 : 4.6.17.rc.1 | updateTime: 71.914 : 3867089  [log_prod/6603014391/2026-10-27/service_mon]', True),
    'updater_update_time': Line(
        'updater', 'OTA updateTime: ', r'OTA updateTime: (?P<s>[\d.]+) sec',
        '2026-10-27 13:17:16,849 - __main__ - INFO - OTA updateTime: 71.914 sec  [log_prod/6603014391/2026-10-27/updater]'),
    'aon_sm': Line(
        'service_mon', 'AON Ver: ', r': 41015 : -?\d+ : AON Ver: (?P<ver>0x[0-9A-Fa-f]+)',
        '1792452409825: 4439: SM: E: 5823: 5823: Service error: APM : 1792452409825 : 41015 : 27 : AON Ver: 0x1B : 27742  [103182502300_2026-10-20/service_mon_c.log]', True),
    'aon_apm': Line(
        'apm', 'get_aon_application_version returns', r'get_aon_application_version returns : (?P<ver>0x[0-9A-Fa-f]+)',
        '1793148920591: 9093: DEVB3: I: 5929: 5929: get_aon_application_version returns : 0x1b  [103102600722_2026-10-28/apm.log]'),
    'obd_fw': Line(
        'obd', 'adapter fw ver : ', r'adapter fw ver : (?P<ver>\S+)',
        '1793126072623: 6728441: J1939: I: 6207: 6207: adapter fw ver : SP4.4  [6603001461_2026-10-28/obd.log]'),
    'band_mask': Line(
        'conn_mgr', 'LTE band pref from device is', r'LTE band pref from device is (?P<mask>[0-9a-fA-F]+)',
        '1793147286093: 2785: CONN_MGR_UTIL_SIERRA: I: 14620: 15144: LTE band pref from device is 300080a  [6603001461_2026-10-28/conn_mgr.log]'),
    'band_ext': Line(
        'conn_mgr', 'LTE band pref ext from device', r'LTE band pref ext from device bits_(?P<range>1_64|65_128) is (?P<mask>[0-9a-fA-F]+)',
        '1793147286093: 2785: CONN_MGR_UTIL_SIERRA: I: 14620: 15144: LTE band pref ext from device bits_65_128 is 42  [6603001461_2026-10-28/conn_mgr.log]'),

    # ── Downtime / data loss ─────────────────────────────────────────────────────────────
    'updater_start': Line(
        'updater', 'Starting OTA update', r'Starting OTA update\s*::',
        '2026-10-27 13:16:04,933 - __main__ - INFO - ::====================::Starting OTA update ::====================::  [log_prod/6603014391/2026-10-27/updater]'),
    'updater_stop': Line(
        'updater', '-c stop -n bagheera', r'Command from file\s+\d+ = sudo /usr/bin/nd_service\.sh -c stop -n bagheera\b',
        '2026-10-27 13:16:19,224 - __main__ - INFO - Command from file  2 = sudo /usr/bin/nd_service.sh -c stop -n bagheera -p /home/ubuntu/.nddevice/4.6.17.rc.1/service/  [log_prod/6603014391/2026-10-27/updater]'),
    'sm_cyclic': Line(
        'service_mon', 'Reboot - Cyclic', r': 40016 : -?\d+ : Reboot - Cyclic',
        '1793568222759: 174564909: SM: E: 3732: 3732: Service error: PWR : 1793568222758 : 40016 : 0 : Reboot - Cyclic : 174608598  [log_prod/6603078588/2026-11-01/service_mon]', True),
    'iot_reboot': Line(
        'awsiot', 'Received command: reboot-phone', r'Received command: reboot-phone',
        '1793698112367: 1255630: IOT-PRSR: I: 3778: 3778: Received command: reboot-phone  [264080392_2026-11-03/awsiot.log]'),
    'cam_crash': Line(
        'ndcentral', 'is crashed', r'NDC: E: \d+: \d+: cam_num (?P<cam>\d+) is crashed',
        '1793577098083: 106579347: NDC: E: 4365: 4598: cam_num 0 is crashed  [log_prod/6603004626/2026-11-02/ndcentral]', True),
    'record_start': Line(
        'ndcentral', 'RECORD_START for cam num',
        r'RECORD_START for cam num (?P<cam>\d+) with session_cnt \d+ received @ epoch_time_ms: (?P<epoch>\d{13})',
        '1793577114647: 3807: NDC: I: 6651: 6651: RECORD_START for cam num 0 with session_cnt 0 received @ epoch_time_ms: 1793577114619, pts_time: 0  [log_prod/6603004626/2026-11-02/ndcentral]'),
    'sm_prev_shutdown': Line(
        'service_mon', 'Previous Shutdown:', r': 40105 : -?\d+ : Previous Shutdown:\s*(?P<reason>\S+) \(Speed',
        '1793058577350: 7676: SM: E: 5855: 5855: Service error: PWR : 1793058577350 : 40105 : 53384 : Previous Shutdown: :DISCHARGE_STATE:RESET_STATE:SHUTDOWN_STATE:SUPERCAP_DISCHARGE:POWER_ON_RESET (Speed: 0.000000) : 30331  [log_prod/103352600191/2026-10-27/service_mon]', True),
    'conn_session_lost': Line(
        'conn_mgr', 'SessionStateCallback:DISCONNECTED', r'SessionStateCallback:DISCONNECTED',
        '1793126957818: 7562195: CONN_MGR_CBK: I: 10800: 10877: SessionStateCallback:DISCONNECTED  [6603001461_2026-10-28/conn_mgr.log]'),
    'conn_exit': Line(
        'conn_mgr', 'Device disconnected : Exiting App', r'Device disconnected : Exiting App',
        '1793103098100: 2155303: CONN_MGR_MAIN: E: 29430: 29509: Device disconnected : Exiting App  [log_prod/6603014391/2026-10-27/conn_mgr]'),
    'data_connected': Line(
        'conn_mgr', 'Data session connected', r'Success : Data session connected',
        '1793107052129: 6497: CONN_MGR_MAIN: I: 11149: 11149: Success : Data session connected  [log_prod/6603014391/2026-10-27/conn_mgr]'),

    # ── Video files / storage ────────────────────────────────────────────────────────────
    'pushed_file': Line(
        'circ_buff', 'Pushed file ',
        r'Pushed file (?P<cam>\d+)_(?P<name>trip\S+?_y)(?P<ext>\.mp4\.ld\.mp4|\.mp4|\.zip) with size (?P<size>\d+) to file_size_check_queue',
        '1793144343499: 8231562: CB: I: 6240: 6240: Pushed file 0_trip1bbf_part00f1c6_33.8681_-117.9309_0.0_1793144282968_y.mp4 with size 38537408 to file_size_check_queue  [103102600722_2026-10-28/circ_buff.log]'),
    'frame_count': Line(
        ('ndcentral', 'cam_rec', 'cam_streamer'), 'Frame count rec', r'CAM(?P<cam>\d+): I: \d+: \d+: Frame count rec(?:ei|ie)ved: (?P<frames>\d+)',
        '1793144465466: 8358899: CAM0: I: 6032: 7147: Frame count recieved: 150   [103102600722_2026-10-28/ndcentral.log] (every 5 s per camera; summed per 10 min in the scan). ndcentral / cam_streamer spell it "recieved", cam_rec (inward / side / DMS cameras on bagheera3, octo) "received"'),
    'storage_info': Line(
        'deviceHealthClient', '"storage_info"',
        r'Received message: \{"storage_info": (?=(?:.*"no_of_hq": \{(?P<hq>[^}]*)\})?)(?=(?:.*"no_of_lq": \{(?P<lq>[^}]*)\})?)(?=(?:.*"no_of_obs": "(?P<obs>[^"]*)")?)',
        '2026-10-27 23:33:25,748 - INFO    - deviceHealthClient        - Received message: {"storage_info": {"timestamp": 1793144002970, "sys_uptime": 132, ... "no_of_hq": {"0": "216/7694.6", ...}, "no_of_lq": {"0": "7639/43084.4", ...}, "no_of_obs": "7594/..."  [103102600722_2026-10-28/deviceHealthClient.log] (values are "file count/MiB")'),
    'obs_ld_size': Line(
        'inference', 'obsZipDestFile size', r"obsZipDestFile size: b'\S+/observations/\S+?\.zip' -- (?P<size>\d+)",
        "2026-10-27 18:30:41,982 - root - INFO - obsZipDestFile size: b'/home/ubuntu/.nddevice/observations/0_trip5689_part05d3c2_33.3346_-117.2435_0.0_1793125756531_y_05468d43cec9da91e04814043039277c.zip' -- 17296  [6603001461_2026-10-28/inference.log]"),
    'disk_free': Line(
        'svc', 'Free space', r'DISK: I: \d+: \d+: Free space (?:at / )?: (?P<bytes>\d+)$',
        '1793141592332: 5485201: DISK: I: 5953: 6208: Free space : 3740762112  [103102600722_2026-10-28/svc.log] (bagheera; krait: "Free space at / : N", other paths such as "at /data/" are not the root partition and are left out)'),
    'disk_used': Line(
        'diagnostic', 'used memory of /dev/mmcblk0 is', r'used memory of /dev/mmcblk0 is (?P<used>[\d.]+)$',
        '1793143416160: 7307740: DIAG: I: 5914: 6141: used memory of /dev/mmcblk0 is 9747  [103102600722_2026-10-28/diagnostic.log] (MiB, the "Used" column of df -m)'),
    'waf': Line(
        'diagnostic', 'Waf is ', r'\b[Ww]af is (?P<waf>[\d.]+)',
        '1793148955065: 41525: WAF_U: I: 6005: 7835: Waf is 1.747442  [103102600722_2026-10-28/diagnostic.log] (1st of a cycle = internal eMMC, 2nd = external; the octo NVMe path logs lower case "waf is")'),

    # ── Installer app / Driveri app (btfv = nd_bt_man) ───────────────────────────────────
    'bt_ignition_on': Line(
        'btfv', 'IGNITION ON received', r'BTMAN: I: \d+: \d+: (?:\[GEN\] )?IGNITION ON received',
        '1793684844100: 7236: BTMAN: I: 3747: 3747: IGNITION ON received  [264080392_2026-11-03/btfv.log]'),
    'installer_scan_start': Line(
        'btfv', 'RegisterInstallerAppFilter', r'Inside: RegisterInstallerAppFilter',
        '1793264889280: 3771: BTMAN: I: 4169: 4169: Inside: RegisterInstallerAppFilter  [105322600008_2026-10-29/btfv.log]'),
    'installer_scan_stop': Line(
        'btfv', ('InstallerApp timeout cb triggered', 'ClearInstallerAppFilter'),
        r'InstallerApp timeout cb triggered|Inside: ClearInstallerAppFilter',
        '1793684964356: 127492: BTMAN: I: 3747: 4022: InstallerApp timeout cb triggered  [264080392_2026-11-03/btfv.log]'),
    'driver_adv_start': Line(
        'btfv', 'Starting Driver Login Advertisement', r'Starting Driver Login Advertisement',
        '1793684844137: 7273: BTMAN: I: 3747: 3747: Starting Driver Login Advertisement  [264080392_2026-11-03/btfv.log]'),
    'driver_scan_start': Line(
        'btfv', 'RegisterDriverLoginLegacyFilter', r'Inside: RegisterDriverLoginLegacyFilter',
        '1793684844136: 7272: BTMAN: I: 3747: 3747: Inside: RegisterDriverLoginLegacyFilter  [264080392_2026-11-03/btfv.log]'),
    'driver_scan_stop': Line(
        'btfv', 'DriverLogin legacy scan complete cb triggered', r'DriverLogin legacy scan complete cb triggered',
        '1793684874335: 37471: BTMAN: I: 3747: 4022: DriverLogin legacy scan complete cb triggered  [264080392_2026-11-03/btfv.log]'),

    # ── Alerts (session key = video name without the leading camera digit) ─────────────
    'alerts_contains': Line(
        'inference', 'alerts.json contains', r'(?<!i)alerts\.json contains (?P<n>\d+) alert\(s\)',
        '2026-10-28 20:37:23,630 - root - INFO - alerts.json contains 1 alert(s)  [103102600722_2026-10-28/inference.log]'),
    'upload_state': Line(
        'inference', 'to UPLOAD_STATE', r'Moving \S*/\d_(?P<key>trip\S+?_y)\.STATE to UPLOAD_STATE\s+alerts found',
        '2026-10-28 20:37:23,649 - root - INFO - Moving /home/iriscli/ND_INPUT/0_trip1bf3_part00f3cb_34.0137_-118.0565_0.0_1793219747843_y.STATE to UPLOAD_STATE   alerts found, exit code 0  [103102600722_2026-10-28/inference.log]'),
    'lla_msg_send': Line(
        ('outwardAnalyticsClient', 'inwardAnalyticsClient', 'dmsAnalyticsClient', 'inertialAnalyticsClient'), 'msg_send: ',
        r'lla_handler\s+- msg_send: (?=.*"videoName": "\d_(?P<key>trip[^"]+?_y)(?:\.mp4)?")(?=.*"uuid": "(?P<uuid>[0-9a-f]{32})")(?=(?:.*"end_timestamp": (?P<end>\d{13}))?)(?=(?:.*"event_code": "(?P<code>[\d.]+)")?)',
        '2026-10-28 14:26:38,588 - INFO    - src.lla_handler           - msg_send: {"videoName": "0_trip1be5_part00f2d1_34.0352_-117.9515_0.0_1793197556451_y.mp4", "inference_data": {"alerts_data": {"alerts": [{"event_code": "2.1.1", "uuid": "0f61851ca1d941a08fa9fc70e29bf68a", "start_timestamp": 179319758789 ...  [103102600722_2026-10-28/outwardAnalyticsClient.log]'),
    'ialerts_found': Line(
        'inference_inertial', 'ialerts.json found at', r'ialerts\.json found at \S*/\d_(?P<key>trip\S+?_y)/ialerts\.json, size=(?P<size>\d+) bytes',
        '2026-11-02 19:17:21,231 - root - INFO - ialerts.json found at /home/iriscli/ND_OUTPUT/0_trip2b1f_part088982_30.4286_-89.0788_0.0_1793635811955_y/ialerts.json, size=415 bytes  [log_prod/6603007848/2026-11-03/inference_inertial] (size 31 = empty list)'),
    'eventdata_summary': Line(
        'unifieduploader', 'Summary file size', r'UPL: I: \d+: (?P<tid>\d+): Summary file size: \d+ name:\s*\S*/\d_(?P<key>trip\S+?_y)_summary\.json\.7z',
        '1793198232440: 1561332: UPL: I: 3381: 3751: Summary file size: 328107 name:\t/dev/shm/0_trip568d_part05d441_32.9981_-117.0829_0.0_1793198116548_y_summary.json.7z  [6603001461_2026-10-28/unifieduploader.log]'),
    'eventdata_call': Line(
        'unifieduploader', 'upload/eventdata', r'UPL: I: \d+: (?P<tid>\d+): Calling service: \S+/upload/eventdata\b',
        '1793198232519: 1561411: UPL: I: 3381: 3751: Calling service: https://idms.netradyne.com/restserver/api/v1/upload/eventdata  [6603001461_2026-10-28/unifieduploader.log]'),
    'eventdata_ok': Line(
        'unifieduploader', 'Upload successful for ', r'Upload successful for \d_(?P<key>trip\S+?_y)\.mp4$',
        '1793219851656: 1559795: UPL: I: 5887: 6361: Upload successful for 0_trip1bf3_part00f3cb_34.0137_-118.0565_0.0_1793219747843_y.mp4  [103102600722_2026-10-28/unifieduploader.log]'),
    'eventdata_fail': Line(
        'unifieduploader', 'Upload failed for ', r'Upload failed for \d_(?P<key>trip\S+?_y)\.mp4$',
        '1793268143193: 261371: UPL: I: 1305: 2748: Upload failed for 0_trip0003_part00000d_91.0000_181.0000_0.0_1793267415178_y.mp4  [105322600008_2026-10-29/unifieduploader.log]'),
    'vod_req': Line(
        'unifieduploader', 'VOD req received', r'VOD req received\. Id: \d+, vod_id: (?P<vod>\S+) , video: \S*/(?P<cam>\d)_(?P<key>trip\S+?_y)\.mp4',
        '1793219859559: 1567698: UPL: I: 5887: 5887: VOD req received. Id: 1023368905, vod_id: vod-v2-1023368905-589283893506-1 , video: /media/data/nd_sdcard/0_trip1bf3_part00f3cb_34.0137_-118.0565_0.0_1793219747843_y.mp4 , cancelled: 0, priority: 5  [103102600722_2026-10-28/unifieduploader.log]'),
    'video_ok': Line(
        'unifieduploader', 'Upload successful for video: ', r'Upload successful for video: \S*/(?P<cam>\d)_(?P<key>trip\S+?_y)\.mp4',
        '1793219883071: 1591210: UPL: I: 5887: 6362: Upload successful for video: /media/data/nd_sdcard/0_trip1bf3_part00f3cb_34.0137_-118.0565_0.0_1793219747843_y.mp4  [103102600722_2026-10-28/unifieduploader.log]'),
    'event_code': Line(
        ('inference', 'inference_inertial'), 'is_alert=',
        r'eventutil - INFO - \d+: is_alert=[01]\s+(?P<code>\d+(?:\.\d+)*) \[.*\] uuid=(?P<uuid>[0-9a-f]+)',
        '2026-10-28 20:37:21,324 - src.eventutil - INFO - 9: is_alert=1  101.1.3.1 [ 38.1s to  41.5s] uuid=d80f856542d846418a8ad4988a276fc1  [103102600722_2026-10-28/inference.log] (first line per uuid kept)'),

    # ── Obs zip upload / retries ─────────────────────────────────────────────────────────
    'obs_req': Line(
        'unifieduploader', 'Upload Observations Request Received', r'UPL: I: \d+: (?P<tid>\d+): Upload Observations Request Received (?P<rt>\d{16})',
        '1793144884738: 8779929: UPL: I: 5828: 6403: Upload Observations Request Received 1793144883745430  [103102600722_2026-10-28/unifieduploader.log]'),
    'obs_zip_start': Line(
        'unifieduploader', 'Creating observations zip file at once', r'UPL: I: \d+: (?P<tid>\d+): Creating observations zip file at once!',
        '1793144885066: 8780257: UPL: I: 5828: 6403: Creating observations zip file at once!  [103102600722_2026-10-28/unifieduploader.log]'),
    'obs_zip_done': Line(
        'unifieduploader', 'Obs zip file created', r'UPL: I: \d+: (?P<tid>\d+): Obs zip file created!',
        '1793144892816: 8788007: UPL: I: 5828: 6403: Obs zip file created!  [103102600722_2026-10-28/unifieduploader.log]'),
    'obs_call': Line(
        'unifieduploader', 'upload/observations', r'UPL: I: \d+: (?P<tid>\d+): Calling service: \S+/upload/observations\b',
        '1793144892999: 8788190: UPL: I: 5828: 6403: Calling service: https://idms.netradyne.com/restserver/api/v1/upload/observations  [103102600722_2026-10-28/unifieduploader.log]'),
    'obs_uploaded': Line(
        'unifieduploader', 'File uploaded. Deleting', r'UPL: I: \d+: (?P<tid>\d+): File uploaded\. Deleting \d+',
        '1793144893723: 8788914: UPL: I: 5828: 6403: File uploaded. Deleting 1  [103102600722_2026-10-28/unifieduploader.log]'),
    'obs_fail': Line(
        'unifieduploader', 'Failed to fetch url', r'UPL: I: \d+: (?P<tid>\d+): ERROR: Failed to fetch url \(\S+/upload/observations\)',
        "1793213578240: 56922: UPL: I: 5984: 6412: ERROR: Failed to fetch url (https://idms.netradyne.com/restserver/api/v1/upload/observations) - curl said: Couldn't resolve host name  [103102600722_2026-10-28/unifieduploader.log]"),
    'retry_details': Line(
        'unifieduploader', 'Details: type - msg type', r'\w+ Details: type - msg type: (?P<type>\d+) msg level',
        '1793268120515: 238693: UPL: I: 1305: 2748: PS Details: type - msg type: 100 msg level: 0  [105322600008_2026-10-29/unifieduploader.log] (logged right after "Retrying... retry_count: n"; 100 eventdata, 101 video, 110 observations)'),
    'vod_attempt_failed': Line(
        'unifieduploader', 'VOD upload attempt failed', r'VOD upload attempt failed: (?P<vod>vod-[^\s,]+)',
        '1793276679063: 1227729: UPL: E: 1211: 2675: VOD upload attempt failed: vod-v2-19665441-2252965591-0, vod_id  [105322600008_2026-10-29/unifieduploader.log]', True),
    'vod_exhausted': Line(
        'unifieduploader', 'Exhausted all upload retries', r'Exhausted all upload retries for (?P<vod>vod-\S+)',
        '1792455673698: 2912304: UPL: C: 5963: 6390: Exhausted all upload retries for vod-v2-19523042-2234900699-0 : 6. Max retry: 6  [103182502300_2026-10-20/unifieduploader_c.log]', True),
    'vod_deleted': Line(
        'unifieduploader', 'deleted VOD upload req', r'deleted VOD upload req \S+ - (?P<vod>vod-\S+) from DB',
        '1793219883086: 1591225: UDB: I: 5887: 6362: deleted VOD upload req /media/data/nd_sdcard/0_trip1bf3_part00f3cb_34.0137_-118.0565_0.0_1793219747843_y.mp4 - vod-v2-1023368905-589283893506-1 from DB. msg_type = 101  [103102600722_2026-10-28/unifieduploader.log]'),
    'videolist_failed': Line(
        'circ_buff', 'collect_notify_videolist is FAILED', r'collect_notify_videolist is FAILED',
        '1793264997847: 107142: CB: I: 4620: 5443: collect_notify_videolist is FAILED  [105322600008_2026-10-29/circ_buff.log]'),
    'videolist_ok': Line(
        'circ_buff', 'collect_notify_videolist is succeded', r'collect_notify_videolist is succeded',
        '1793144354479: 8242542: CB: I: 6240: 7648: collect_notify_videolist is succeded  [103102600722_2026-10-28/circ_buff.log]'),
    'health_post_failed': Line(
        'health', 'curl exited', r'curl_with_health: curl exited (?P<code>\d+)',
        '2026-10-28 07:42:02,449 - health - ERROR - curl_with_health: curl exited 6: % Total    % Received ...  [6603001461_2026-10-28/health.log]'),
    'health_ok': Line(
        'health', 'Upload of Health Stats successful', r'Upload of Health Stats successful',
        '2026-10-27 23:33:09,424 - health - INFO - Upload of Health Stats successful  [103102600722_2026-10-28/health.log]'),

    # ── LLA (low latency alert; uuid = the alert uuid of lla_msg_send) ──────────────────
    'ndc_lla_message': Line(
        'ndcentral', 'lla_message :{', r'NDC: C: \d+: \d+: lla_message :\{',
        '1793198169230: 1495306: NDC: C: 3808: 5800: lla_message :{"inference_data": {"alerts_data": {"alerts": [{"speed_info": 68.029000458893947, "end_timestamp": 1793198167449, ...  [6603001461_2026-10-28/ndcentral.log] (cut at ~300 chars, so paired by time)', True),
    'lla_hs_add': Line(
        'unifieduploader', '"uploadadd"', r'"lla_(?P<uuid>[0-9a-f]{32})_(?P<n>\d+)": \{"uploadadd": (?P<add>\d{13})\}',
        '1793198169246: 1498138: UPL: I: 3381: 3625: sending msg to hs--: {"session": "0_trip568d_part05d441_32.9981_-117.0829_0.0_1793198116548_y", "alert_info": {"lla_4294e556792744d8bf466d02c4d1f98f_0": {"uploadadd": 1793198169244}}}  [6603001461_2026-10-28/unifieduploader.log]'),
    'lla_hs_result': Line(
        'unifieduploader', '"uploadstart"',
        r'"lla_(?P<uuid>[0-9a-f]{32})_(?P<n>\d+)": \{"uploadstart": (?P<start>\d{13}), "uploadend": (?P<end>\d{13}), "status": "(?P<status>pass|fail)"',
        '1793149279280: 2060651: UPL: I: 3391: 3684: sending msg to hs--: {"session": "0_trip568a_part05d3f9_33.1186_-117.1010_0.0_1793149263671_y", "alert_info": {"lla_2580cfa0a3204cefbac94ea93ae879a0_0": {"uploadstart": 1793149278811, "uploadend": 1793149279280, "status": "pass", ...  [6603001461_2026-10-28/unifieduploader.log]'),
}

# Services whose detailed log is also read for its gaps: the last line before a silence of
# ALIVE_GAP_MS or more, and the file's last line ('alive' hits). Used as "last line before
# the reboot / power cut" by the downtime and watchdog metrics.
ALIVE_SERVICES = ('ndcentral', 'power_mon', 'svc', 'conn_mgr')
ALIVE_GAP_MS = 20_000

# Lines that come in large numbers: reduced in the day scan
#   bucket: {key: (bucket ms, group to sum)} -> one hit per bucket and the other groups, with
#           the sum and n (lines); once: {key: group} -> the first line per value of the group
BUCKET = {'frame_count': (600_000, 'frames')}
ONCE = {'event_code': 'uuid'}

CAMS = {0: 'Outward', 1: 'Inward', 2: 'Left', 3: 'Right',
        4: 'MDVR 4', 5: 'MDVR 5', 6: 'MDVR 6', 7: 'MDVR 7', 8: 'DMS'}
# Cameras only some product lines have (MDVR, DMS): their columns are left out of a report
# when no device on it logged them
OPTIONAL_CAMS = {4, 5, 6, 7, 8}

# ════════════════════════════════════════════════════════════════════════════════════════
# 2. KPI_TABLES. Metric(id, name, unit, agg, fn, lines, how)
#    agg (device value from its samples): sum | mean | max | last | pct (100 x mean of 0/1)
#    fn: compute function (section 3) returning [(ts, value)]; None = no log line found
#    cam: the camera of a per-camera column of OPTIONAL_CAMS (else None): computed only for
#         devices that have the camera, the column shown only when some device has a value
# ════════════════════════════════════════════════════════════════════════════════════════

Metric = namedtuple('Metric', 'id name unit agg fn lines how cam')
Metric.__new__.__defaults__ = (None,)
Table = namedtuple('Table', 'id title metrics note')


def _tables():
    # Built in a function so the compute helpers (section 3) can be defined below it
    def M(*args, cam=None):
        return Metric(*args, cam=cam if cam in OPTIONAL_CAMS else None)
    cam_names = sorted(CAMS.items())
    return [
        Table('kpi-alerts', 'Alerts Summary', [
            M('alerts_detected', 'Total Alerts Detected', 'count', 'sum', _sum_group('alerts_contains', 'n'),
              ['alerts_contains'], 'sum of N over "alerts.json contains N alert(s)" (final alert list of each session)'),
            M('alerts_cards', 'Total Alerts Event Cards published', 'count', 'sum', _count('eventdata_ok', unique=('key',)),
              ['eventdata_ok'], 'distinct sessions with "Upload successful for <session>.mp4" (event data upload)'),
            *[M(f'alerts_video_cam{c}', f'Total Alerts Video uploaded - Cam {c}' + (f' ({n})' if c > 3 else ''), 'count', 'sum',
                _count('video_ok', unique=('cam', 'key'), where=lambda g, c=c: g['cam'] == str(c)),
                ['video_ok'], f'distinct videos of cam {c} ({n}) with "Upload successful for video: .../{c}_<session>.mp4"', cam=c)
              for c, n in cam_names],
            M('alerts_unique', 'Total Unique', 'count', 'sum', _count('upload_state', unique=('key',)),
              ['upload_state'], 'distinct alert sessions moved to UPLOAD_STATE ("alerts found")'),
        ], 'Counts over the boots of each device.'),

        Table('kpi-boot', 'Bootup, Video Recording & Connectivity', [
            M('boot_power_on', 'Device Bootup Time (Power ON)', 's', 'mean', _boot_start_since_power_on,
              ['syslog_uptime', 'ndc_system_uptime', 'pwr_uptime'],
              'PARTIAL: power-on itself (left LED) is not logged; kernel start (anchor) to the first service start of the boot'),
            M('boot_reboot_log', 'Device Bootup Time (Reboot Log)', 's', 'mean', _first_since_boot('reboot_entering'),
              ['reboot_entering'], 'power-on anchor to the first "@Reboot Entering" of the boot'),
            M('system_uptime', 'System Uptime (NDCentral)', 's', 'mean', _first_value(('ndc_system_uptime', 's', 1)),
              ['ndc_system_uptime'], 'value of ndcentral "System Uptime: N seconds" (system uptime when ndcentral starts)'),
            M('service_uptime', 'Service Uptime (NDCentral)', 's', 'mean', _first_value(('ndc_service_uptime', 's', 1)),
              ['ndc_service_uptime'], 'first ndcentral "serviceUpTime: N" of the boot (printed at the first video session)'),
            M('modem_enum', 'Modem Enumeration', 's', 'mean', _first_value(('gps_enum', 's', 1), ('sm_gps_enum', 'ms', 0.001)),
              ['gps_enum', 'sm_gps_enum'], 'system uptime at "GPS Port Enumerate Time" (modem tty enumerated)'),
            M('svc_start', 'SVC Service Start time', 's', 'mean', _first_since_boot('svc_start'),
              ['svc_start'], 'power-on anchor to "######Starting SVC######"'),
            M('analytics_start', 'Analytics Service Start time', 's', 'mean', _first_since_boot('analytics_start'),
              ['analytics_start'], 'power-on anchor to "######## Starting Analytics Service"'),
            M('zmq_socket', 'ZMQ Socket Creation', 's', 'mean', _first_since_boot('zmq_socket'),
              ['zmq_socket'], 'power-on anchor to cam_rec "SUCCESS IN CREATE zmq_socket" (bagheera; krait has no cam_rec)'),
            M('first_frame', 'First Frame Capture from Device Power ON', 's', 'mean', _first_since_boot('first_frame', 'epoch'),
              ['first_frame'], 'power-on anchor to "first frame for cam num 0 registered@"'),
            M('hw_button', 'HW Button Detection - LED Glow for User Generated Alert', 's', 'mean', _first_since_boot('button_ready'),
              ['button_ready'], 'PARTIAL: power-on anchor to svc "Button event thread created successfully" (button ready, incl. service uptime)'),
            M('data_connectivity', 'Connection Manager - Data Connectivity time', 's', 'mean', _first_since_boot('lte_session'),
              ['lte_session'], 'power-on anchor to "LTE Data Session started successfully"'),
            M('keep_alive_reboot', 'Keep Alive @ Reboot', 's', 'mean', _first_since_boot('ka_reached'),
              ['ka_reached'], 'power-on anchor to the first power_mon keep alive "reached_cloud 1"'),
            M('gps_ttff_lpw', 'GPS TTFF - First Boot after Low Power Wakeup', 's', 'mean',
              _first_value(('gps_ttff', 's', 1), boot_filter=_boot_after_lpw),
              ['gps_ttff', 'prev_shutdown'], '"GPS First Fix time: N sec" (system uptime at fix) in boots whose previous_shutdown_reason is a LOWPOWER one'),
            M('gps_ttff_cyclic', 'GPS TTFF - Cyclic Reboot / Crank Change', 's', 'mean',
              _first_value(('gps_ttff', 's', 1), boot_filter=_boot_after_cyclic_or_crank),
              ['gps_ttff', 'prev_shutdown', 'wakeup_reason'], 'same, in boots after a CYCLIC reboot or woken by IGNITION / crank'),
            M('cyclic_reboot_uptime', 'Cyclic Reboot Time (max_uptime)', 'h', 'mean', _cyclic_reboot_uptime,
              ['uptime_long', 'soak_done', 'shutdown_reason'], 'uptime_secs of the last "check_uptime" before "CYCLIC_REBOOT: Soak done Ns, rebooting", when the reboot followed (SHUTDOWN_FOR_CYCLIC_REBOOT)'),
            M('cyclic_crank_high', 'Cyclic Reboot for Crank High Detection', 's', 'mean', _between('pass_high', 'soak_done', start='last'),
              ['pass_high', 'soak_done'], 'last "CYCLIC_REBOOT: Uptime Check Pass HIGH" to "Soak done ..., rebooting" (the soak)'),
            M('shutdown_time', 'Device Shutdown Time', 's', 'mean', _between('shutdown_initiated', 'shutdown_exec', start='last'),
              ['shutdown_initiated', 'shutdown_exec'], '"Device Shutdown Initiated" to "system_execute cmd: shutdown now|poweroff|reboot" (the configured delay before it, e.g. 720 s after ignition off, is not included)'),
        ], 'Seconds from power on (kernel start: epoch minus the system uptime the boot logs) unless said otherwise; detailed logs only.'),

        Table('kpi-ota', 'OTA & Firmware Details', [
            M('ota_size', 'OTA Package Size', 'MB', 'last', _values('ota_size', 'bytes', 1 / 1e6),
              ['ota_size'], '"OTA package size in bytes = N" (otacheck / updater)'),
            M('ota_update_time', 'OTA Update time', 's', 'last', _first_value(('sm_update_time', 's', 1), ('updater_update_time', 's', 1), every=True),
              ['sm_update_time', 'updater_update_time'], 'updateEngine "updateTime: N" (install only, not the download)'),
            M('aon_version', 'AON Firmware Version', 'text', 'last', _last_text(('aon_sm', 'ver'), ('aon_apm', 'ver'), fmt=lambda v: v.upper().replace('0X', '0x')),
              ['aon_sm', 'aon_apm'], '"AON Ver: 0x.." (service_mon 41015) or apm "get_aon_application_version returns"; bagheera only'),
            M('isp_version', 'ISP Firmware Version', 'text', 'last', None, [],
              'NOT_FOUND: no ISP firmware version in any uploaded log'),
            M('obd_version', 'OBD Firmware', 'text', 'last', _last_text(('obd_fw', 'ver')),
              ['obd_fw'], 'obd "adapter fw ver : X"'),
            M('bands', 'Bands Enabled', 'text', 'last', _bands,
              ['band_mask', 'band_ext'], 'PARTIAL: Sierra modems; LTE band preference masks decoded to band numbers (Quectel logs no mask)'),
        ], 'Latest value on each device.'),

        Table('kpi-downtime', 'Device Downtime / Data Loss', [
            M('down_ota', 'During OTA Update', 's', 'mean', _between('updater_stop', 'first_frame'),
              ['updater_stop', 'first_frame'], 'updater "-c stop -n bagheera" to the next "first frame for cam num 0"'),
            M('down_cyclic', 'Device Cyclic Reboot', 's', 'mean', _between('sm_cyclic', 'first_frame'),
              ['sm_cyclic', 'first_frame'], '"Reboot - Cyclic" (service_mon 40016) to the next boot\'s first frame'),
            M('down_iot', 'Device Reboot from AWS IoT / Cloud Command', 's', 'mean', _between('iot_reboot', 'first_frame'),
              ['iot_reboot', 'first_frame'], 'awsiot "Received command: reboot-phone" to the next first frame'),
            M('down_cam_crash', 'Service Restart due to Camera Crash', 's', 'mean', _between('cam_crash', 'record_start', pair='cam'),
              ['cam_crash', 'record_start'], '"cam_num N is crashed" to the next "RECORD_START for cam num N"'),
            M('down_abrupt', 'Device Abrupt Power Cycle (Without Supercap)', 's', 'mean', _abrupt_power(supercap=False),
              ['sm_prev_shutdown', 'first_frame'], 'boot whose "Previous Shutdown:" is a bare POWER_DOWN: last ndcentral/power_mon/svc line before it to its first frame'),
            M('down_abrupt_supercap', 'Device Abrupt Power Cycle (With Supercap)', 's', 'mean', _abrupt_power(supercap=True),
              ['sm_prev_shutdown', 'first_frame'], 'same, for a "Previous Shutdown:" with SUPERCAP_DISCHARGE'),
            M('down_abrupt_reboot', 'Device Reboot time after Abrupt Power Cycle (till video recording start)', 's', 'mean', _abrupt_reboot_time,
              ['sm_prev_shutdown', 'first_frame'], 'power-on anchor to the first frame, in the boots after an abrupt power cycle'),
            M('down_lte_ota', 'Connection Manager - LTE Outage during OTA Update', 's', 'mean', _lte_outage_ota,
              ['updater_start', 'conn_session_lost', 'conn_exit', 'data_connected'],
              'PARTIAL: after "::Starting OTA update::", the last conn_mgr line / DISCONNECTED before the next "Success : Data session connected" to it'),
            M('down_lte_disconnect', 'Connection Manager - LTE Outage during Device Disconnection', 's', 'mean',
              _between('conn_exit', 'data_connected', same_power_on=True),
              ['conn_exit', 'data_connected'], 'PARTIAL: "Device disconnected : Exiting App" to the next "Success : Data session connected" without a reboot in between (at shutdown it is the device being off)'),
        ], 'Seconds of recording / data lost per event, averaged over the events of each device.'),

        Table('kpi-crash', 'Camera Crash Stats', [
            *[m for c, n in cam_names for m in (
                M(f'crash_cam{c}', f'{n} Camera Crash Count - Total', 'count', 'sum',
                  _count('cam_crash', unique=('cam', '_sec'), where=lambda g, c=c: g['cam'] == str(c)),
                  ['cam_crash'], f'"cam_num {c} is crashed" (one per second)', cam=c),
                M(f'crash_cam{c}_pct', f'{n} Camera Crash Count %', '%', 'pct', _crash_boots(c),
                  ['cam_crash'], f'% of boots with drive time that had a cam {c} crash', cam=c))],
        ], 'Number of devices: the rows; total drive time: the drive min column.'),

        Table('kpi-video-hd', 'Video File Size Details - HD', [
            *[M(f'hd_size_cam{c}', f'{n} Video - File Size - HD', 'MB', 'mean', _file_size(c, '.mp4', 1e6),
                ['pushed_file'], f'circ_buff "Pushed file {c}_<session>.mp4 with size N" (last per file, files < 10 KB dropped)', cam=c)
              for c, n in cam_names],
            *[M(f'hd_fps_cam{c}', f'{n} Video - FPS - HD', 'fps', 'mean', _fps(c),
                ['frame_count'], f'"CAM{c}: Frame count received: N" every 5 s: frames / time', cam=c)
              for c, n in cam_names],
        ], 'Average over the files / 5 s windows of each device.'),

        Table('kpi-video-ld', 'Video File Size Details - LD', [
            M('ld_size_cam0', 'Outward Video - File Size - LD', 'MB', 'mean', _file_size(0, '.mp4.ld.mp4', 1e6), ['pushed_file'], '"Pushed file 0_<session>.mp4.ld.mp4 with size N"'),
            M('ld_size_cam1', 'Inward Video - File Size - LD', 'MB', 'mean', _file_size(1, '.mp4.ld.mp4', 1e6), ['pushed_file'], '"Pushed file 1_<session>.mp4.ld.mp4 with size N"'),
            M('ld_size_cam2', 'Left Video - File Size - LD', 'MB', 'mean', None, [], 'NOT_FOUND: left camera writes one (already LD) file a minute, see the HD size'),
            M('ld_size_cam3', 'Right Video - File Size - LD', 'MB', 'mean', None, [], 'NOT_FOUND: right camera writes one (already LD) file a minute, see the HD size'),
            *[M(f'ld_size_cam{c}', f'{n} Video - File Size - LD', 'MB', 'mean', _file_size(c, '.mp4.ld.mp4', 1e6),
                ['pushed_file'], f'"Pushed file {c}_<session>.mp4.ld.mp4 with size N"', cam=c)
              for c, n in cam_names if c in OPTIONAL_CAMS],
            M('ld_fps_cam0', 'Outward Video - FPS - LD', 'fps', 'mean', _fps(0), ['frame_count'], 'PARTIAL: LD is encoded from the same frames, so the HD frame count'),
            M('ld_fps_cam1', 'Inward Video - FPS - LD', 'fps', 'mean', _fps(1), ['frame_count'], 'PARTIAL: LD is encoded from the same frames, so the HD frame count'),
        ], 'Average over the files of each device.'),

        Table('kpi-preview', 'Preview LQ Videos - Video File Size Details', [
            M('pv_ld_cam0', 'Outward - LD File Size (Average)', 'MB', 'last', _storage_avg('lq', 0), ['storage_info'], 'PARTIAL: storage_info no_of_lq["0"] MiB / file count'),
            M('pv_ld_cam1', 'Inward - LD File Size (Average)', 'MB', 'last', _storage_avg('lq', 1), ['storage_info'], 'PARTIAL: storage_info no_of_lq["1"] MiB / file count'),
            M('pv_hd_cam0', 'Outward - HD File Size (Average)', 'MB', 'last', _storage_avg('hq', 0), ['storage_info'], 'PARTIAL: storage_info no_of_hq["0"] MiB / file count'),
            M('pv_hd_cam1', 'Inward - HD File Size (Average)', 'MB', 'last', _storage_avg('hq', 1), ['storage_info'], 'PARTIAL: storage_info no_of_hq["1"] MiB / file count'),
        ], 'Average size of the files on the card (deviceHealthClient storage_info), latest per device.'),

        Table('kpi-obs', 'Obs File Size Details', [
            M('obs_alloc', 'Size Allocated for Obs Files (/home/ubuntu/.nddevice/observations)', 'MB', 'last', None, [], 'NOT_FOUND: no quota is logged'),
            M('obs_hd_max', 'Max HD Obs files can be stored - File Count', 'count', 'last', _storage_count('obs'),
              ['storage_info'], 'PARTIAL: obs files on the card (storage_info no_of_obs), the steady state once the card is full'),
            M('obs_hd_avg', 'HD Obs - Avg size of file', 'KB', 'mean', _file_size(0, '.zip', 1e3), ['pushed_file'], 'circ_buff "Pushed file 0_<session>.zip with size N" (last per file)'),
            M('obs_ld_max', 'Max LD Obs files can be stored - File Count', 'count', 'last', None, [], 'NOT_FOUND: needs the obs allocation, which is not logged'),
            M('obs_ld_avg', 'LD Obs - Avg size of file', 'KB', 'mean', _values('obs_ld_size', 'size', 1e-3), ['obs_ld_size'], 'inference "obsZipDestFile size: b\'.../observations/...zip\' -- N"'),
            M('obs_gps_size', 'Obs File Size (One zip with valid GPS)', 'KB', 'mean', _file_size(0, '.zip', 1e3, valid_gps=True),
              ['pushed_file'], 'HD obs zips whose name has a GPS fix (not 91.0000_181.0000)'),
        ], 'Sizes averaged over the files of each device.'),

        Table('kpi-emmc', 'SdCard / eMMC', [
            M('emmc_cam0', 'Total Outward files in eMMC', 'count', 'last', _storage_count('hq', 0, 'lq'), ['storage_info'], 'storage_info no_of_hq["0"] + no_of_lq["0"] file counts'),
            M('emmc_cam1', 'Total Inward files in eMMC', 'count', 'last', _storage_count('hq', 1, 'lq'), ['storage_info'], 'storage_info no_of_hq["1"] + no_of_lq["1"]'),
            M('emmc_cam2', 'Total Left files in eMMC', 'count', 'last', _storage_count('hq', 2), ['storage_info'], 'storage_info no_of_hq["2"]'),
            M('emmc_cam3', 'Total Right files in eMMC', 'count', 'last', _storage_count('hq', 3), ['storage_info'], 'storage_info no_of_hq["3"]'),
            M('emmc_ext', 'Total External camera (MDVR / DMS) files in eMMC', 'count', 'last', _storage_count('hq', 'N', 'lq'), ['storage_info'],
              'storage_info no_of_hq["N"] + no_of_lq["N"]: the external cameras together (no key per camera 4-8 is sent)'),
            M('emmc_obs', 'Obs files in eMMC', 'count', 'last', _storage_count('obs'), ['storage_info'], 'storage_info no_of_obs'),
            M('emmc_hours', 'eMMC - Storage Hours', 'h', 'last', _storage_hours, ['storage_info'], 'outward LD files / 60 (one file a minute), as test TC_circbuff_777'),
        ], 'Latest storage_info of each device (meaningful once the card is full).'),

        Table('kpi-root', 'Internal Storage - /root', [
            M('root_size', 'Storage Size', 'GB', 'last', _root_size, ['disk_free', 'disk_used'],
              'PARTIAL: svc "DISK: Free space" of / + diagnostic "used memory of /dev/mmcblk0" (MiB)'),
        ], ''),

        Table('kpi-waf', 'WAF - eMMC Metrics', [
            M('waf_ext', 'External eMMC', 'WAF', 'last', _waf(1), ['waf'], '2nd "Waf is X" of a diagnostic WAF cycle (bagheera only)'),
            M('waf_int', 'Internal eMMC', 'WAF', 'last', _waf(0), ['waf'], '1st "Waf is X" of a diagnostic WAF cycle'),
        ], 'Write amplification factor, latest per device.'),

        Table('kpi-watchdog', 'Tegra Watchdog Timeout', [
            M('wdt_resets', 'Reboot Initiated', 'count', 'sum', _watchdog_count,
              ['wakeup_reason', 'prev_shutdown'], 'boots reset by the watchdog (RESET_REASON / previous_shutdown_reason with WATCHDOG); the watchdog firing itself is not logged'),
            M('wdt_pm_start', 'Power Monitor Start after reboot', 's', 'mean', _first_since_boot('pm_start', boot_filter=_boot_after_watchdog),
              ['pm_start'], 'power-on anchor to "#####STARTING POWER MONITOR#####" in the watchdog boot'),
            M('wdt_reboot_time', 'Time taken to Reboot', 's', 'mean', _watchdog_reboot_time,
              ['pm_start'], 'PARTIAL: last ndcentral/power_mon/svc line of the hung boot to the power monitor start'),
        ], ''),

        Table('kpi-installer', 'Installer App - Metrics', [
            M('inst_ign', 'Ignition ON Detected', 's', 'mean', _first_since_boot('bt_ignition_on'), ['bt_ignition_on'], 'power-on anchor to btfv "IGNITION ON received"'),
            M('inst_scan_start', 'Scan for Installer App Starts', 's', 'mean', _after_ignition('installer_scan_start'), ['bt_ignition_on', 'installer_scan_start'], 'IGNITION ON to "Inside: RegisterInstallerAppFilter"'),
            M('inst_scan_stop', 'Scan for Installer App Stops', 's', 'mean', _after_ignition('installer_scan_stop'), ['bt_ignition_on', 'installer_scan_stop'], 'IGNITION ON to "InstallerApp timeout cb triggered"'),
        ], 'Seconds after btfv ignition on; only builds with the installer scan (9.x, EU) log it.'),

        Table('kpi-driveri', 'Driveri App & BT Beacon - Metrics', [
            M('dl_ign', 'Ignition ON Detected', 's', 'mean', _first_since_boot('bt_ignition_on'), ['bt_ignition_on'], 'power-on anchor to btfv "IGNITION ON received"'),
            M('dl_adv', 'Advertising Started', 's', 'mean', _after_ignition('driver_adv_start'), ['bt_ignition_on', 'driver_adv_start'], 'IGNITION ON to "Starting Driver Login Advertisement"'),
            M('dl_scan_start', 'Scan for DriveriApp / BT Beacon Starts', 's', 'mean', _after_ignition('driver_scan_start'), ['bt_ignition_on', 'driver_scan_start'], 'IGNITION ON to "Inside: RegisterDriverLoginLegacyFilter"'),
            M('dl_scan_stop', 'Scan for DriveriApp / BT Beacon Stops', 's', 'mean', _after_ignition('driver_scan_stop'), ['bt_ignition_on', 'driver_scan_stop'], 'IGNITION ON to the first "DriverLogin legacy scan complete cb triggered"'),
        ], 'Seconds after btfv ignition on; only when no driver is logged in.'),

        Table('kpi-alert-upload', 'Alert Upload Time - Metrics', [
            M('au_rec_start', 'File Recording Start time', 's', 'mean', _alert_step('rec'), ['upload_state', 'lla_msg_send', 'ialerts_found'],
              'recording start of the alert file (epoch in its name) to the alert detection'),
            M('au_inertial', 'Inertial Alert Detection', 's', 'mean', _alert_step('inertial'), ['ialerts_found'],
              'PARTIAL: recording start to inference_inertial "ialerts.json found at .../<session>/" (non-empty)'),
            M('au_vision', 'Inference / Vision Alert Detection / Processing', 's', 'mean', _alert_step('vision'), ['lla_msg_send', 'upload_state'],
              'recording start to the earliest of the analytics "msg_send" (real time) and inference UPLOAD_STATE'),
            M('au_api_trigger', 'Alert Notification to the Cloud - API Call Triggering', 's', 'mean', _alert_step('api_call'), ['eventdata_summary', 'eventdata_call'],
              'detection to the first /upload/eventdata call of the session (session: the "Summary file size" line before it on the same thread)'),
            M('au_api_ok', 'Alert Notification to the Cloud - API Call Successful', 's', 'mean', _alert_step('api_ok'), ['eventdata_ok'],
              'detection to "Upload successful for <session>.mp4"'),
            *[M(f'au_vod_req_cam{c}', f'Upload Video Request Received from Cloud - {n}', 's', 'mean', _alert_step('vod_req', c), ['vod_req'],
                f'detection to the first "VOD req received ... video: .../{c}_<session>.mp4"', cam=c) for c, n in cam_names],
            *[M(f'au_video_ok_cam{c}', f'Upload Video Successful - {n}', 's', 'mean', _alert_step('video_ok', c), ['video_ok'],
                f'detection to "Upload successful for video: .../{c}_<session>.mp4"', cam=c) for c, n in cam_names],
            M('au_total', 'Total Time Taken for upload (from alert detection)', 's', 'mean', _alert_step('total'), ['video_ok'],
              'detection to the last camera\'s video upload success of the session'),
        ], 'Seconds per alert session, averaged. Detection = earliest of real-time msg_send, inertial ialerts.json and '
           'inference UPLOAD_STATE. "Alert Notification" is the event data upload (every alert has one); the LLA call is in the LLA table.'),

        Table('kpi-obs-upload', 'Obs Data Zip Upload - Metrics', [
            M('ou_received', 'Upload Request for Obs zip - Received', 's', 'mean', _obs_step('received'), ['obs_req'],
              'request time (rt, in the line) to "Upload Observations Request Received"'),
            M('ou_files_added', 'Files adding into zip - Completion (10 files)', 's', 'mean', _obs_step('zip_start'), ['obs_req', 'obs_zip_start'],
              'request received to "Creating observations zip file at once!" (last file added)'),
            M('ou_zip', 'Creating Obs Zip', 's', 'mean', _obs_step('zip'), ['obs_zip_start', 'obs_zip_done'],
              '"Creating observations zip file at once!" to "Obs zip file created!"'),
            M('ou_upload_start', 'Uploading Obs Zip - Start', 's', 'mean', _obs_step('call'), ['obs_req', 'obs_call'],
              'request received to "Calling service: .../upload/observations"'),
            M('ou_upload_ok', 'Uploading Obs Zip - Successful', 's', 'mean', _obs_step('uploaded'), ['obs_req', 'obs_uploaded'],
              'request received to "File uploaded. Deleting"'),
        ], 'Seconds per obs batch (unifieduploader thread of the request), averaged.'),

        Table('kpi-retry', 'Retry Count & Duration for API / Upload Calls', [
            M('rt_eventdata_n', 'upload-eventdata - Retry Count', 'count', 'sum', _count('retry_details', where=lambda g: g['type'] == '100'),
              ['retry_details'], '"Details: type - msg type: 100" (one per retry)'),
            M('rt_eventdata_s', 'upload-eventdata - Retry Duration', 's', 'mean', _between('eventdata_fail', 'eventdata_ok', pair='key'),
              ['eventdata_fail', 'eventdata_ok'], 'first "Upload failed for <session>.mp4" to its "Upload successful"'),
            M('rt_video_n', 'upload-video - Retry Count', 'count', 'sum', _vod_retries,
              ['vod_attempt_failed', 'vod_exhausted'], '"VOD upload attempt failed" minus "Exhausted all upload retries" (critical logs too)'),
            M('rt_video_s', 'upload-video - Retry Duration', 's', 'mean', _between('vod_attempt_failed', 'vod_deleted', pair='vod'),
              ['vod_attempt_failed', 'vod_deleted'], 'first failed attempt of a vod_id to "deleted VOD upload req ... - <vod_id> from DB" (uploaded or given up)'),
            M('rt_obs_n', 'upload-observationdata - Retry Count', 'count', 'sum', _count('retry_details', where=lambda g: g['type'] == '110'),
              ['retry_details'], '"Details: type - msg type: 110"'),
            M('rt_obs_s', 'upload-observationdata - Retry Duration', 's', 'mean', _between('obs_fail', 'obs_uploaded', pair='tid'),
              ['obs_fail', 'obs_uploaded'], 'first "Failed to fetch url (.../upload/observations)" to the next "File uploaded" on that thread'),
            M('rt_videolist_n', 'videolist - Retry Count', 'count', 'sum', _count('videolist_failed'),
              ['videolist_failed'], 'PARTIAL: "collect_notify_videolist is FAILED" (the next periodic call is the retry)'),
            M('rt_videolist_s', 'videolist - Retry Duration', 's', 'mean', _between('videolist_failed', 'videolist_ok'),
              ['videolist_failed', 'videolist_ok'], 'PARTIAL: first FAILED to the next "is succeded"'),
            M('rt_status_n', 'upload-device-status - Retry Count', 'count', 'sum', _count('health_post_failed'),
              ['health_post_failed'], 'health "curl_with_health: curl exited N" (failed status POST, re-sent from backup)'),
            M('rt_status_s', 'upload-device-status - Retry Duration', 's', 'mean', _between('health_post_failed', 'health_ok'),
              ['health_post_failed', 'health_ok'], 'first failed POST to the next "Upload of Health Stats successful"'),
        ], 'Counts summed over the boots; durations in seconds per failure streak, averaged.'),

        Table('kpi-lla', 'LLA - Metrics', [
            M('lla_rec_start', 'File Recording Start time', 's', 'mean', _lla_step('rec'), ['lla_msg_send'],
              'recording start of the alert file (epoch in videoName) to the analytics "msg_send" (LLA detection)'),
            M('lla_ndc_create', 'NDCentral LLA creation', 's', 'mean', _lla_step('ndc'), ['lla_msg_send', 'ndc_lla_message'],
              'msg_send to the next ndcentral "lla_message :{"'),
            M('lla_ndc_notify', 'NDCentral LLA notifying to uploader', 's', 'mean', _lla_step('add'), ['lla_msg_send', 'lla_hs_add'],
              'msg_send to the uploader "lla_<uuid>_0": {"uploadadd": T} (hand-off received)'),
            M('lla_api_trigger', 'Uploader LLA Notification to the Cloud - API Call Triggering', 's', 'mean', _lla_step('start'), ['lla_hs_result'],
              'msg_send to "uploadstart" of attempt 0'),
            M('lla_api_ok', 'Uploader LLA Notification to the Cloud - API Call Successful', 's', 'mean', _lla_step('pass'), ['lla_hs_result'],
              'msg_send to "uploadend" of the attempt with status pass'),
            M('lla_total', 'Total Time Taken for upload (from alert detection)', 's', 'mean', _lla_step('total'), ['lla_msg_send', 'lla_hs_result'],
              'alert end_timestamp (in msg_send) to the passing "uploadend"'),
            M('lla_retry_n', 'LLA retry count', 'count', 'sum', _lla_step('retries'), ['lla_hs_result'], 'highest attempt n of "lla_<uuid>_<n>" per LLA'),
            M('lla_retry_s', 'LLA retry duration', 's', 'mean', _lla_step('retry_time'), ['lla_hs_result'],
              'uploadend of attempt 0 to the passing (or last) attempt\'s uploadend, for LLAs that retried'),
        ], 'Seconds per LLA (alert uuid), averaged. LLA is sent only for whitelisted real-time codes.'),
    ]


# ════════════════════════════════════════════════════════════════════════════════════════
# 3. Engine
# ════════════════════════════════════════════════════════════════════════════════════════

_COMPILED = {k: re.compile(line.regex) for k, line in KPI_LOG_LINES.items()}
REC_EPOCH_RE = re.compile(r'_(\d{13})_y$')
NAME_GPS_RE = re.compile(r'_(-?\d+\.\d+)_(-?\d+\.\d+)_[\d.]+_\d{13}_y$')
NO_FIX = ('91.0000', '181.0000')
POWER_ON_CLUSTER_MS = 5_000
# Anchor of a report boot: the power-on at most this long before its first service start
POWER_ON_LEAD_MS = 120_000
# "First line after power on" values longer than this are another event, not the boot's
MAX_SINCE_POWER_ON_S = 900
# Ignition ON -> BT advertising / scan: later ones are re-triggered (driver idle, logout)
MAX_AFTER_IGNITION_S = 300
# Lines only in detailed logs: a boot with one of these could show any KPI line
DETAILED_MARKERS = ('alive', 'ndc_service_uptime', 'syslog_uptime', 'pwr_uptime', 'record_start')
# Lines with a camera number: a device has the cameras they name
CAM_LINES = ('record_start', 'pushed_file', 'frame_count', 'cam_crash', 'vod_req', 'video_ok')
MIN_FILE_BYTES = 10_000  # placeholder files (privacy mode, krait2 external cams) are < 100 bytes
WAF_CYCLE_MS = 5_000


def _services(line):
    return line.service if isinstance(line.service, tuple) else (line.service,)


def _greps(line):
    return line.grep if isinstance(line.grep, tuple) else (line.grep,)


def _bre(literal):
    """grep -G pattern matching the plain text literal."""
    return re.sub(r'([.\[\]*^$\\])', r'\\\1', literal)


def service_files(logs_dir, service):
    """The service's log files of a day: <service>.log* and <service>_c.log* (exact name, so
    'inference' is not inference_inertial); 'syslogs' is the syslog monitor folder."""
    if service == 'syslogs':
        return sorted(p for p in (logs_dir / 'syslogs').glob('*.log*') if p.is_file())
    name = re.compile(rf'{re.escape(service)}(?:_c)?\.log')
    return sorted(p for p in logs_dir.iterdir() if p.is_file() and name.match(p.name))


AWK_GAPS = (r'/^[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]:/ '
            r'{ t = substr($0, 1, 13) + 0; if (p && t - p > GAP) print p; p = t } END { if (p) print p }')


def _alive_points(path):
    """Epoch ms of the last line before each silence of ALIVE_GAP_MS or more, and of the last line."""
    try:
        out = subprocess.run(['awk', '-v', f'GAP={ALIVE_GAP_MS}', AWK_GAPS, str(path)],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [int(float(x)) for x in out.split()]


def scan_day(logs_dir, iter_hits):
    """[(key, ts, ((group, value), ...))] of every KPI line in one day's logs directory.

    iter_hits: log_search.iter_service_hits. One grep pass per service; lines repeated in
    <service>.log and <service>_c.log are kept once.
    """
    by_service = defaultdict(list)
    for key, line in KPI_LOG_LINES.items():
        for service in _services(line):
            by_service[service].append(key)
    hits = set()
    for service, keys in by_service.items():
        files = service_files(logs_dir, service)
        if not files:
            continue
        pats, owner = [], []
        for key in keys:
            for literal in _greps(KPI_LOG_LINES[key]):
                pats.append((_bre(literal), KPI_LOG_LINES[key].regex))
                owner.append(key)
        for ts, matched, text in iter_hits(files, pats):
            for key in {owner[i] for i in matched}:
                m = _COMPILED[key].search(text)
                if m:
                    hits.add((key, ts, tuple(sorted((g, v) for g, v in m.groupdict().items() if v is not None))))
    for service in ALIVE_SERVICES:
        path = logs_dir / f'{service}.log'
        if path.is_file():
            hits.update(('alive', ts, (('svc', service),)) for ts in _alive_points(path))
    return _reduce(sorted(hits, key=lambda h: h[1]))


def _reduce(hits):
    """BUCKET / ONCE keys reduced (see their definitions); hits sorted by time."""
    out, buckets, seen = [], {}, set()
    for key, ts, groups in hits:
        if key in BUCKET:
            size, total = BUCKET[key]
            g = dict(groups)
            rest = tuple((k, v) for k, v in groups if k != total)
            b = buckets.setdefault((key, ts // size, rest), [ts, 0, 0])
            b[1] += int(g[total])
            b[2] += 1
        elif key in ONCE:
            ident = (key, dict(groups).get(ONCE[key]))
            if ident not in seen:
                seen.add(ident)
                out.append((key, ts, groups))
        else:
            out.append((key, ts, groups))
    for (key, _, rest), (ts, total, n) in buckets.items():
        out.append((key, ts, rest + ((BUCKET[key][1], str(total)), ('n', str(n)))))
    return out


Hit = namedtuple('Hit', 'ts g')


class Device:
    """A device's KPI hits over all its days, its power-ons and its boots.

    boots: every boot with a start (also those outside the date range, so times are never
    given to the wrong boot); details: {boot index: boot detail} of the reported boots
    (their 'drive_minutes' are used by the crash %).
    Power-ons ("segments", see the module docstring) are what the since-power-on metrics are
    measured in; boot_of() places any time in a report boot.
    """

    def __init__(self, hits, boots, details=None):
        self.lines = defaultdict(list)
        seen = set()
        for key, ts, groups in sorted(hits, key=lambda h: (h[1], h[0])):
            if key in ONCE:
                ident = (key, dict(groups).get(ONCE[key]))
                if ident in seen:
                    continue
                seen.add(ident)
            self.lines[key].append(Hit(ts, dict(groups)))
        self.power_ons = self._power_ons()
        self.boots = sorted((b for b in boots if b.get('start') is not None), key=lambda b: b['start'])
        # A boot starts at its power-on when that is just before its first service start
        self.boot_anchor = {}
        for b in self.boots:
            i = bisect.bisect_right(self.power_ons, b['start']) - 1
            if i >= 0 and b['start'] - self.power_ons[i] <= POWER_ON_LEAD_MS:
                self.boot_anchor[b['index']] = self.power_ons[i]
        self._starts = []
        for b in self.boots:
            eff = self.boot_anchor.get(b['index'], b['start'])
            self._starts.append(max(eff, self._starts[-1] + 1) if self._starts else eff)
        self.details = details or {}
        self._cache = {}

    def _power_ons(self):
        """Sorted epoch ms of the power-ons: uptime-line anchors (epoch - uptime) clustered,
        each taken from the preferred source (syslog > ndcentral > power_mon)."""
        cands = sorted((h.ts - float(h.g['s']) * 1000, pri)
                       for pri, key in enumerate(('syslog_uptime', 'ndc_system_uptime', 'pwr_uptime'))
                       for h in self.lines.get(key, []))
        clusters = []
        for t, pri in cands:
            if clusters and t - clusters[-1][0][0] <= POWER_ON_CLUSTER_MS:
                clusters[-1].append((t, pri))
            else:
                clusters.append([(t, pri)])
        return [round(min(c, key=lambda x: (x[1], x[0]))[0]) for c in clusters]

    def hits(self, *keys):
        if len(keys) == 1:
            return self.lines.get(keys[0], [])
        return sorted((h for k in keys for h in self.lines.get(k, [])), key=lambda h: h.ts)

    def boot_of(self, ts):
        """Index of the report boot ts falls in, or None (before the first boot)."""
        i = bisect.bisect_right(self._starts, ts) - 1
        if i < 0:
            return None
        b = self.boots[i]
        return b['index'] if b['end'] is None or i + 1 < len(self.boots) or ts < b['end'] else None

    def segment_of(self, ts):
        """Index of the power-on ts falls after, or None."""
        i = bisect.bisect_right(self.power_ons, ts) - 1
        return i if i >= 0 else None

    def by_segment(self, *keys):
        """{power-on index: [hits]} (cached)."""
        if ('seg', keys) not in self._cache:
            out = defaultdict(list)
            for h in self.hits(*keys):
                i = self.segment_of(h.ts)
                if i is not None:
                    out[i].append(h)
            self._cache[('seg', keys)] = out
        return self._cache[('seg', keys)]

    def by_boot(self, *keys):
        """{report boot index: [hits]} (cached)."""
        if ('boot', keys) not in self._cache:
            out = defaultdict(list)
            for h in self.hits(*keys):
                b = self.boot_of(h.ts)
                if b is not None:
                    out[b].append(h)
            self._cache[('boot', keys)] = out
        return self._cache[('boot', keys)]

    def units(self, *keys):
        """{unit: [hits]}: by power-on when the device logged uptime lines, else by report boot
        (critical-only logs have no uptime lines)."""
        return self.by_segment(*keys) if self.power_ons else self.by_boot(*keys)

    def anchor(self, unit):
        """Power-on epoch ms of a unit of units(), or None."""
        return self.power_ons[unit] if self.power_ons else None

    def alive_before(self, ts, services=None):
        """Epoch ms of the last line logged before ts (an 'alive' point), or None."""
        stamps = [h.ts for h in self.hits('alive') if not services or h.g['svc'] in services]
        i = bisect.bisect_left(stamps, ts)
        return stamps[i - 1] if i else None

    def reasons(self, unit):
        """Text of the previous-shutdown / wakeup / reset reason lines of a unit of units()."""
        parts = []
        for key in ('prev_shutdown', 'sm_prev_shutdown', 'wakeup_reason'):
            for h in self.units(key).get(unit, [])[:1]:
                parts.extend(h.g.values())
        return ' '.join(parts)

    def cams(self):
        """Camera numbers the device logged: recording, files (placeholders too), frames,
        crashes, video requests / uploads."""
        if 'cams' not in self._cache:
            self._cache['cams'] = {int(h.g['cam']) for k in CAM_LINES for h in self.hits(k)}
        return self._cache['cams']

    def detailed_boots(self):
        return {self.boot_of(h.ts) for h in self.hits(*DETAILED_MARKERS)}


# ── compute helpers: each returns fn(device) -> [(ts, value)]; ts picks the boot ─────────

def _first_since_boot(key, group=None, boot_filter=None):
    """Seconds from power on to the first key line after it (group: the epoch in the line)."""
    def fn(d):
        out = []
        for u, hits in d.units(key).items():
            anchor = d.anchor(u)
            if anchor is None or (boot_filter and not boot_filter(d, u)):
                continue
            h = hits[0]
            t = int(h.g[group]) if group else h.ts
            if 0 <= t - anchor <= MAX_SINCE_POWER_ON_S * 1000:
                out.append((h.ts, (t - anchor) / 1000))
        return out
    return fn


def _first_value(*sources, boot_filter=None, every=False):
    """First (or every, with every=True) value per boot of the first source that has one;
    sources: (key, group, scale)."""
    def fn(d):
        out, done = [], set()
        for key, group, scale in sources:
            for u, hits in d.units(key).items():
                if u in done or (boot_filter and not boot_filter(d, u)):
                    continue
                done.add(u)
                out.extend((h.ts, float(h.g[group]) * scale) for h in (hits if every else hits[:1]))
        return out
    return fn


def _values(key, group, scale=1.0):
    return lambda d: [(h.ts, float(h.g[group]) * scale) for h in d.hits(key)]


def _sum_group(key, group):
    return lambda d: [(h.ts, int(h.g[group])) for h in d.hits(key)]


def _last_text(*sources, fmt=None):
    def fn(d):
        out = []
        for key, group in sources:
            out.extend((h.ts, fmt(h.g[group]) if fmt else h.g[group]) for h in d.hits(key))
        return sorted(out)
    return fn


def _count(key, unique=None, where=None):
    """1 per line (per distinct value of the unique groups over the device; '_sec' = the second)."""
    def fn(d):
        out, seen = [], set()
        for h in d.hits(key):
            if where and not where(h.g):
                continue
            if unique:
                ident = tuple(h.ts // 1000 if u == '_sec' else h.g.get(u) for u in unique)
                if ident in seen:
                    continue
                seen.add(ident)
            out.append((h.ts, 1))
        return out
    return fn


def _between(start_keys, end_keys, pair=None, start='first', same_power_on=False):
    """Seconds from a start line to the next end line (same value of the pair group).
    Several starts before one end (a streak of failures, a repeated check): start='first'
    measures from the first of them, start='last' from the one just before the end.
    same_power_on: only when no power-on lies between them."""
    start_keys = start_keys if isinstance(start_keys, tuple) else (start_keys,)
    end_keys = end_keys if isinstance(end_keys, tuple) else (end_keys,)

    def fn(d):
        ends = defaultdict(list)
        for h in d.hits(*end_keys):
            ends[h.g.get(pair) if pair else None].append(h.ts)
        pending = {}  # pair value -> (end ts, start ts) of the current streak
        out = []

        def close(pv):
            end_ts, start_ts = pending.pop(pv)
            if not same_power_on or d.segment_of(start_ts) == d.segment_of(end_ts):
                out.append((end_ts, (end_ts - start_ts) / 1000))

        for s in d.hits(*start_keys):
            pv = s.g.get(pair) if pair else None
            if pv in pending and s.ts < pending[pv][0]:
                if start == 'last':
                    pending[pv] = (pending[pv][0], s.ts)
                continue
            if pv in pending:
                close(pv)
            stamps = ends.get(pv, [])
            i = bisect.bisect_left(stamps, s.ts)
            if i < len(stamps):
                pending[pv] = (stamps[i], s.ts)
        for pv in list(pending):
            close(pv)
        return sorted(out)
    return fn


def _boot_start_since_power_on(d):
    return [(b['start'], (b['start'] - d.boot_anchor[b['index']]) / 1000)
            for b in d.boots if b['index'] in d.boot_anchor]


def _boot_after_lpw(d, u):
    return 'LOWPOWER' in d.reasons(u)


def _boot_after_cyclic_or_crank(d, u):
    reasons = d.reasons(u)
    return 'CYCLIC' in reasons or 'IGNITION' in reasons or 'CRANK' in reasons


def _boot_after_watchdog(d, u):
    return 'WATCHDOG' in d.reasons(u)


def _cyclic_reboot_uptime(d):
    """Hours of uptime (last check_uptime) at "Soak done, rebooting", when the cyclic reboot
    was not cancelled (a SHUTDOWN_FOR_CYCLIC_REBOOT restart follows within 2 min)."""
    restarts = [h.ts for h in d.hits('shutdown_reason') if 'CYCLIC' in h.g['reason'] and h.g['mode'] == 'restarting']
    uptimes = d.hits('uptime_long')
    stamps = [h.ts for h in uptimes]
    out = []
    for h in d.hits('soak_done'):
        i = bisect.bisect_left(restarts, h.ts)
        j = bisect.bisect_right(stamps, h.ts) - 1
        if i < len(restarts) and restarts[i] - h.ts <= 120_000 and j >= 0 and h.ts - stamps[j] <= 60_000:
            out.append((h.ts, int(uptimes[j].g['s']) / 3600))
    return out


def _bands(d):
    """LTE band numbers from the Sierra band preference masks (bit n = band n+1; ext mask
    bits_65_128: bit n = band n+65)."""
    out = []
    for _, hits in d.by_boot('band_mask', 'band_ext').items():
        bands = set()
        for h in hits:
            base = 64 if h.g.get('range') == '65_128' else 0
            mask = int(h.g['mask'], 16)
            bands.update(base + n + 1 for n in range(mask.bit_length()) if mask >> n & 1)
        if bands:
            out.append((hits[-1].ts, ','.join(f'B{n}' for n in sorted(bands))))
    return out


def _abrupt_boots(d, supercap):
    """Power-ons whose service_mon "Previous Shutdown:" was an abrupt power loss."""
    result = []
    for u, hits in d.units('sm_prev_shutdown').items():
        reason = hits[0].g['reason']
        if 'SOFT_REBOOT' in reason:
            continue
        if supercap and 'SUPERCAP_DISCHARGE' in reason:
            result.append(u)
        elif not supercap and 'SUPERCAP_DISCHARGE' not in reason and re.match(r':?(?:SHUTDOWN_STATE:)?POWER_DOWN\b', reason):
            result.append(u)
    return result


def _abrupt_power(supercap):
    def fn(d):
        out = []
        frames = d.units('first_frame')
        for u in _abrupt_boots(d, supercap):
            anchor = d.anchor(u)
            last = d.alive_before(anchor) if anchor is not None else None
            if frames.get(u) and last is not None:
                out.append((frames[u][0].ts, (frames[u][0].ts - last) / 1000))
        return out
    return fn


def _abrupt_reboot_time(d):
    frames = d.units('first_frame')
    out = []
    for u in _abrupt_boots(d, True) + _abrupt_boots(d, False):
        anchor = d.anchor(u)
        if frames.get(u) and anchor is not None and frames[u][0].ts - anchor <= MAX_SINCE_POWER_ON_S * 1000:
            out.append((frames[u][0].ts, (frames[u][0].ts - anchor) / 1000))
    return out


def _lte_outage_ota(d):
    connected = [h.ts for h in d.hits('data_connected')]
    lost = [h.ts for h in d.hits('conn_session_lost', 'conn_exit')]
    out = []
    starts = d.hits('updater_start')
    for n, s in enumerate(starts):
        i = bisect.bisect_right(connected, s.ts)
        if i == len(connected) or connected[i] - s.ts > 30 * 60_000:
            continue
        up = connected[i]
        if n + 1 < len(starts) and up > starts[n + 1].ts:
            continue  # this attempt did not drop the data session; the next one did
        # The outage starts at a DISCONNECTED / exit line, or where conn_mgr went quiet (it is
        # killed and restarted by the update)
        cands = [t for t in lost if s.ts <= t < up]
        quiet = d.alive_before(up, services=('conn_mgr',))
        if quiet is not None and quiet >= s.ts:
            cands.append(quiet)
        if cands:
            out.append((up, (up - max(cands)) / 1000))
    return out


def _crash_boots(cam):
    """1 / 0 per boot with drive time: whether the camera crashed in it."""
    def fn(d):
        crashed = {b for b, hits in d.by_boot('cam_crash').items() if any(h.g['cam'] == str(cam) for h in hits)}
        return [(det['start'], int(i in crashed)) for i, det in d.details.items()
                if det.get('start') is not None and det.get('drive_minutes')]
    return fn


def _last_per_file(d, cam, ext):
    files = {}
    for h in d.hits('pushed_file'):
        if h.g['cam'] == str(cam) and h.g['ext'] == ext:
            files[h.g['name']] = h
    return files


def _file_size(cam, ext, unit, valid_gps=False):
    def fn(d):
        out = []
        for name, h in _last_per_file(d, cam, ext).items():
            size = int(h.g['size'])
            if size < MIN_FILE_BYTES:
                continue
            if valid_gps:
                m = NAME_GPS_RE.search(name)
                if not m or m.groups() == NO_FIX:
                    continue
            out.append((h.ts, size / unit))
        return out
    return fn


def _fps(cam):
    """Frames received per second from the 10-min buckets of "Frame count received" (5 s windows)."""
    return lambda d: [(h.ts, int(h.g['frames']) / (int(h.g['n']) * 5)) for h in d.hits('frame_count')
                      if h.g['cam'] == str(cam) and int(h.g['n'])]


def _storage_bucket(text, cam):
    """(count, MiB) of a storage_info bucket ('"0": "216/7694.6", ...'), or None."""
    m = re.search(rf'"{cam}": "(\d+)/([\d.]+)"', text or '')
    return (int(m.group(1)), float(m.group(2))) if m else None


def _storage_avg(kind, cam):
    def fn(d):
        out = []
        for h in d.hits('storage_info'):
            bucket = _storage_bucket(h.g.get(kind), cam)
            if bucket and bucket[0]:
                out.append((h.ts, bucket[1] * 1.048576 / bucket[0]))  # MiB -> MB
        return out
    return fn


def _storage_count(kind, cam=None, plus=None):
    def fn(d):
        out = []
        for h in d.hits('storage_info'):
            if kind == 'obs':
                m = re.match(r'(\d+)/', h.g.get('obs') or '')
                if m:
                    out.append((h.ts, int(m.group(1))))
                continue
            bucket = _storage_bucket(h.g.get(kind), cam)
            if bucket:
                extra = _storage_bucket(h.g.get(plus), cam) if plus else None
                out.append((h.ts, bucket[0] + (extra[0] if extra else 0)))
        return out
    return fn


def _storage_hours(d):
    return [(ts, n / 60) for ts, n in _storage_count('lq', 0)(d)]


def _root_size(d):
    """GB of the root partition: free bytes of / + used MiB (df -m Used), nearest lines."""
    used = d.hits('disk_used')
    stamps = [h.ts for h in used]
    out = []
    for h in d.hits('disk_free'):
        i = bisect.bisect_right(stamps, h.ts) - 1
        if i < 0 or h.ts - stamps[i] > 15 * 60_000:
            continue
        total = int(h.g['bytes']) + float(used[i].g['used']) * 1024 ** 2
        out.append((h.ts, total / 1e9))
    return out


def _waf(position):
    """WAF of the position-th line of each diagnostic cycle (0 internal, 1 external)."""
    def fn(d):
        out, cycle = [], []
        for h in d.hits('waf') + [Hit(float('inf'), {})]:
            if cycle and h.ts - cycle[-1].ts > WAF_CYCLE_MS:
                if len(cycle) > position:
                    out.append((cycle[position].ts, float(cycle[position].g['waf'])))
                cycle = []
            cycle.append(h)
        return out
    return fn


def _watchdog_count(d):
    return [(d.power_ons[u], 1) for u in range(len(d.power_ons)) if _boot_after_watchdog(d, u)]


def _watchdog_reboot_time(d):
    """Last line before the watchdog power-on (the hung boot) to the power monitor start."""
    out = []
    for u, hits in d.by_segment('pm_start').items():
        if not _boot_after_watchdog(d, u):
            continue
        last = d.alive_before(d.power_ons[u])
        if last is not None:
            out.append((hits[0].ts, (hits[0].ts - last) / 1000))
    return out


def _after_ignition(key):
    """Seconds from the power-on's first btfv IGNITION ON to the first key line after it
    (within MAX_AFTER_IGNITION_S)."""
    def fn(d):
        out = []
        targets = d.units(key)
        for u, ign in d.units('bt_ignition_on').items():
            after = [h for h in targets.get(u, []) if h.ts >= ign[0].ts]
            if after and after[0].ts - ign[0].ts <= MAX_AFTER_IGNITION_S * 1000:
                out.append((after[0].ts, (after[0].ts - ign[0].ts) / 1000))
        return out
    return fn


def _rec_epoch(key):
    m = REC_EPOCH_RE.search(key or '')
    return int(m.group(1)) if m else None


def _alert_sessions(d):
    """{session key: {'det', 'rec', 'inertial', 'vision', 'api_call', 'api_ok', ('vod_req', cam),
    ('video_ok', cam)}} epoch ms of each step of an alert session."""
    if 'alerts' in d._cache:
        return d._cache['alerts']
    s = defaultdict(dict)

    def first(key, step, ts):
        if step not in s[key] or ts < s[key][step]:
            s[key][step] = ts

    for h in d.hits('lla_msg_send'):
        first(h.g['key'], 'rt', h.ts)
    for h in d.hits('upload_state'):
        first(h.g['key'], 'nrt', h.ts)
    for h in d.hits('ialerts_found'):
        if int(h.g['size']) > 31:  # 31 bytes = empty alert list
            first(h.g['key'], 'inertial', h.ts)
    sessions = {k: v for k, v in s.items() if v}
    # eventdata calls carry no session: it is the last "Summary file size" on the same thread
    last_summary = {}
    for h in d.hits('eventdata_summary', 'eventdata_call'):
        if 'key' in h.g:
            last_summary[h.g['tid']] = h.g['key']
        elif h.g['tid'] in last_summary and last_summary[h.g['tid']] in sessions:
            first(last_summary[h.g['tid']], 'api_call', h.ts)
    for h in d.hits('eventdata_ok'):
        if h.g['key'] in sessions:
            first(h.g['key'], 'api_ok', h.ts)
    for h in d.hits('vod_req'):
        if h.g['key'] in sessions:
            first(h.g['key'], ('vod_req', int(h.g['cam'])), h.ts)
    for h in d.hits('video_ok'):
        if h.g['key'] in sessions:
            first(h.g['key'], ('video_ok', int(h.g['cam'])), h.ts)
    for key, steps in sessions.items():
        found = [steps[k] for k in ('rt', 'nrt', 'inertial') if k in steps]
        steps['det'] = min(found)
        steps['rec'] = _rec_epoch(key)
        vision = [steps[k] for k in ('rt', 'nrt') if k in steps]
        if vision:
            steps['vision'] = min(vision)
    d._cache['alerts'] = sessions
    return sessions


def _alert_step(step, cam=None):
    def fn(d):
        out = []
        for steps in _alert_sessions(d).values():
            det, rec = steps['det'], steps['rec']
            if step == 'rec':
                if rec:
                    out.append((det, (det - rec) / 1000))
            elif step in ('inertial', 'vision'):
                if rec and step in steps:
                    out.append((steps[step], (steps[step] - rec) / 1000))
            elif step == 'total':
                done = [t for k, t in steps.items() if isinstance(k, tuple) and k[0] == 'video_ok']
                if done and max(done) >= det:
                    out.append((max(done), (max(done) - det) / 1000))
            else:
                t = steps.get((step, cam) if cam is not None else step)
                if t is not None and t >= det:
                    out.append((t, (t - det) / 1000))
        return out
    return fn


def _obs_batches(d):
    """[{'rt', 'req', 'zip_start', 'zip_done', 'call', 'uploaded'}] per obs batch: the lines
    after an "Upload Observations Request Received" on the same thread, up to the next one."""
    if 'obs' in d._cache:
        return d._cache['obs']
    steps = {'obs_zip_start': 'zip_start', 'obs_zip_done': 'zip_done', 'obs_call': 'call', 'obs_uploaded': 'uploaded'}
    batches, current = [], {}
    keyed = sorted(((h.ts, k, h) for k in ('obs_req', *steps) for h in d.hits(k)), key=lambda x: (x[0], x[1] != 'obs_req'))
    for ts, key, h in keyed:
        tid = h.g['tid']
        if key == 'obs_req':
            current[tid] = {'rt': int(h.g['rt']) // 1000, 'req': ts}
            batches.append(current[tid])
        elif tid in current and steps[key] not in current[tid]:
            current[tid][steps[key]] = ts
    d._cache['obs'] = batches
    return batches


def _obs_step(step):
    def fn(d):
        out, seen_rt = [], set()
        for b in _obs_batches(d):
            if step == 'received':
                # The request time repeats for the next batches and retries: first one only
                if b['rt'] not in seen_rt and b['req'] >= b['rt']:
                    out.append((b['req'], (b['req'] - b['rt']) / 1000))
                seen_rt.add(b['rt'])
            elif step == 'zip':
                if 'zip_start' in b and 'zip_done' in b:
                    out.append((b['zip_done'], (b['zip_done'] - b['zip_start']) / 1000))
            elif step in b:
                out.append((b[step], (b[step] - b['req']) / 1000))
        return out
    return fn


def _vod_retries(d):
    return [(h.ts, 1) for h in d.hits('vod_attempt_failed')] + [(h.ts, -1) for h in d.hits('vod_exhausted')]


def _llas(d):
    """{uuid: {'det', 'rec', 'end', 'ndc', 'add', 'start', 'pass', 'retries', 'retry_time'}}."""
    if 'lla' in d._cache:
        return d._cache['lla']
    llas = {}
    for h in d.hits('lla_msg_send'):
        llas.setdefault(h.g['uuid'], {'det': h.ts, 'rec': _rec_epoch(h.g['key']),
                                       'end': int(h.g['end']) if h.g.get('end') else None})
    ndc = [h.ts for h in d.hits('ndc_lla_message')]
    for lla in llas.values():
        i = bisect.bisect_left(ndc, lla['det'])
        if i < len(ndc) and ndc[i] - lla['det'] <= 30_000:
            lla['ndc'] = ndc[i]
    for h in d.hits('lla_hs_add'):
        lla = llas.get(h.g['uuid'])
        if lla is not None and h.g['n'] == '0':
            lla.setdefault('add', int(h.g['add']))
    attempts = defaultdict(list)
    for h in d.hits('lla_hs_result'):
        if h.g['uuid'] in llas:
            attempts[h.g['uuid']].append((int(h.g['n']), int(h.g['start']), int(h.g['end']), h.g['status']))
    for uuid, tries in attempts.items():
        lla, tries = llas[uuid], sorted(set(tries))
        lla['start'] = tries[0][1]
        passed = [t for t in tries if t[3] == 'pass']
        if passed:
            lla['pass'] = passed[0][2]
        lla['retries'] = tries[-1][0]
        if tries[-1][0] > 0:
            lla['retry_time'] = (passed[0][2] if passed else tries[-1][2]) - tries[0][2]
    d._cache['lla'] = llas
    return llas


def _lla_step(step):
    def fn(d):
        out = []
        for lla in _llas(d).values():
            det = lla['det']
            if step == 'rec':
                if lla['rec']:
                    out.append((det, (det - lla['rec']) / 1000))
            elif step == 'total':
                if 'pass' in lla and lla['end']:
                    out.append((lla['pass'], (lla['pass'] - lla['end']) / 1000))
            elif step == 'retries':
                if 'retries' in lla:
                    out.append((det, lla['retries']))
            elif step == 'retry_time':
                if 'retry_time' in lla:
                    out.append((det, lla['retry_time'] / 1000))
            elif step in lla and lla[step] >= det:
                out.append((lla[step], (lla[step] - det) / 1000))
        return out
    return fn


KPI_TABLES = _tables()
METRICS = {m.id: m for t in KPI_TABLES for m in t.metrics}


# ── per boot / device / OTA ──────────────────────────────────────────────────────────────

def compute(device, boot_indexes):
    """{boot index: {metric id: [values]}, '_codes': {code: unique events}} for the given
    boots (the reported ones) from a Device; every sample goes to the boot its time is in."""
    wanted = set(boot_indexes)
    result = {b: defaultdict(list) for b in wanted}
    detailed = device.detailed_boots() & wanted
    for metric in METRICS.values():
        if not metric.fn or (metric.cam is not None and metric.cam not in device.cams()):
            continue
        if metric.agg == 'sum':
            # 0 where the logs could show the line (not empty)
            crit = all(KPI_LOG_LINES[k].crit for k in metric.lines)
            for b in (wanted if crit else detailed):
                result[b][metric.id].append(0)
        for ts, value in sorted(metric.fn(device), key=lambda x: x[0]):
            b = device.boot_of(ts)
            if b in wanted:
                result[b][metric.id].append(value)
    for h in device.hits('event_code'):
        b = device.boot_of(h.ts)
        if b in wanted:
            codes = result[b].setdefault('_codes', Counter())
            codes[h.g['code']] += 1
    return {b: dict(v) for b, v in result.items()}


def _agg(agg, values):
    if not values:
        return None
    if agg == 'sum':
        return max(0, sum(values))
    if agg == 'last':
        return values[-1]
    if agg == 'max':
        return max(values)
    mean = sum(values) / len(values)
    return 100 * mean if agg == 'pct' else mean


def boot_values(samples):
    """{metric id: value} of one boot's compute() samples (stored with the boot)."""
    out = {mid: _agg(METRICS[mid].agg, v) for mid, v in samples.items() if mid in METRICS}
    out = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in out.items() if v is not None}
    if samples.get('_codes'):
        out['_codes'] = dict(samples['_codes'])
    return out


def summarize(boot_samples):
    """Device level from the compute() samples of its boots on one OTA (in time order):
    {'metrics': {id: [value, n samples]}, 'event_codes': {code: count}}."""
    merged, codes = defaultdict(list), Counter()
    for samples in boot_samples:
        for mid, values in samples.items():
            if mid == '_codes':
                codes.update(values)
            else:
                merged[mid].extend(values)
    metrics = {}
    for mid, values in merged.items():
        value = _agg(METRICS[mid].agg, values)
        if value is not None:
            metrics[mid] = [round(value, 3) if isinstance(value, float) else value, len(values)]
    return {'metrics': metrics, 'event_codes': dict(codes)}


def p90(values):
    """90th percentile (linear interpolation, like numpy's default) of numbers, or None."""
    values = sorted(v for v in values if isinstance(v, (int, float)))
    if not values:
        return None
    k = (len(values) - 1) * 0.9
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def merge_summaries(device_summaries):
    """OTA level from {device: summarize()}: per metric the devices' values, P90, total
    (sum metrics; boot-weighted for %), most common text; event codes with devices."""
    devices = {d: (s or {}).get('metrics', {}) for d, s in device_summaries.items()}
    columns = {}
    for mid, metric in METRICS.items():
        vals = [m[mid] for m in devices.values() if mid in m]
        col = {'devices': len(vals)}
        if metric.unit == 'text':
            common = Counter(v[0] for v in vals).most_common(1)
            col['common'] = common[0][0] if common else None
        else:
            col['p90'] = p90([v[0] for v in vals])
            if metric.agg == 'sum':
                col['total'] = sum(v[0] for v in vals)
            elif metric.agg == 'pct':
                n = sum(v[1] for v in vals)
                col['total'] = sum(v[0] * v[1] for v in vals) / n if n else None
        columns[mid] = col
    codes, code_devices = Counter(), Counter()
    per_device_codes = defaultdict(list)
    for s in device_summaries.values():
        for code, n in ((s or {}).get('event_codes') or {}).items():
            codes[code] += n
            code_devices[code] += 1
            per_device_codes[code].append(n)
    event_codes = [{'code': c, 'count': n, 'devices': code_devices[c], 'p90': p90(per_device_codes[c])}
                   for c, n in codes.most_common()]
    return {'devices': {d: {mid: v[0] for mid, v in m.items()} for d, m in devices.items()},
            'columns': columns, 'event_codes': event_codes}
