import os
import sys
import time
import threading
import json
import gc
import ctypes
import queue
import tempfile
import configparser
import socket
from datetime import datetime
import re
import traceback
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from urllib.parse import urlparse
from datetime import datetime, timezone
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from contextlib import contextmanager

import pandas as pd
import py7zr
import clickhouse_connect

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.path.pardir))
sys.path.append(REPO_ROOT)

from db_login.db_login import db_connect_pool
from lib.logger import Logger
from lib.date_range import update_registry_data


# observation_data/video_metadata live in ClickHouse; see db_credentials.ini's
# CLICKHOUSE_DB section (override via DP_CLICKHOUSE_SECTION for a different one).
CLICKHOUSE_CONFIG_SECTION = os.getenv('DP_CLICKHOUSE_SECTION', 'CLICKHOUSE_DB')
CLICKHOUSE_DB_CONFIG_PATH = os.path.join(REPO_ROOT, 'db_credentials.ini')

# observation_data columns whose extracted Python value is a dict/list that must be
# serialized to a JSON string before insertion (ClickHouse stores these as String).
CLICKHOUSE_JSON_COLUMNS = {
    'canmetadata', 'alerts_data', 'audio_events_data', 'events_data', 'speed_data',
    'user_generated_alert', 'idling_report', 'fuel_report', 'session_embedding', 'burst_mode',
}
# observation_data columns typed Nullable(UInt8)/UInt8 that receive a Python bool/None.
CLICKHOUSE_BOOL_COLUMNS = {
    'inertial_processed', 'vision_processed', 'is_inward_processed', 'is_dms_processed',
    'is_inward_cam_obstructed', 'has_multi_lane', 'has_road_boundary_tracks',
    'has_ipc_events', 'is_hd_file', 'inward_vision_processed', 'faceImageCaptured',
}
# observation_data columns typed Array(String) - keep list-valued, never None.
CLICKHOUSE_ARRAY_COLUMNS = {'inward_models_processed', 'outward_models_processed', 'dms_models_processed'}
# observation_data columns kept as Nullable(String) even though the source value looks numeric.
CLICKHOUSE_TEXT_COLUMNS = {'product_line', 'udid', 'starttime', 'starttimeld', 'inwardstarttime', 'inwardstarttimeld', 'rtc_valid', 'file_timestamp'}

VIDEO_METADATA_COLUMNS = [
    'file_name', 'device_id', 'start_time', 'end_time', 'seq_no',
    'valid', 'altitude', 'bearing', 'accuracy', 'lat', 'long',
    'speed', 'raw_timestamp', 'altitudeMSL', 'timestamp',
]

CLICKHOUSE_OBSERVATION_DATA_DDL = """
    CREATE TABLE IF NOT EXISTS observation_data
    (
        ota Nullable(String), product_line Nullable(String), udid Nullable(String), file_name String,
        file_timestamp Nullable(Float64), start_time Nullable(DateTime64(3)),
        end_time Nullable(DateTime64(3)), ignition_status Nullable(Int32),
        uptime Nullable(Int64), service_uptime Nullable(Int64), privacymode Nullable(Int32),
        dismode Nullable(String), voltage Nullable(Float64), processing_mode Nullable(Int32),
        inertial_processed Nullable(UInt8), vision_processed Nullable(UInt8),
        nrt_status Nullable(String), tripno Nullable(String), videometadatastatus Nullable(UInt32),
        min_speed Nullable(Float32), max_speed Nullable(Float32), sensormetadata_count Nullable(UInt32),
        driverinvariantsession Nullable(String), driverid Nullable(String), vehclass Nullable(String),
        vehicleid Nullable(String), cameras Nullable(Int32), prevvideoname Nullable(String),
        current_videoname Nullable(String), nextvideoname Nullable(String),
        devicemodes_itemscount Nullable(UInt32), inference_data_itemscount Nullable(UInt32),
        canmetadata Nullable(String), alerts_data_num_alerts Nullable(UInt32), alerts_data Nullable(String),
        audio_events_num_alerts Nullable(UInt32), audio_events_data Nullable(String),
        events_data_num_alerts Nullable(UInt32), events_data Nullable(String),
        metadatastatus String DEFAULT 'full', device_id String, s3_path Nullable(String),
        speed_data Nullable(String), starttime Nullable(String), starttimeld Nullable(String),
        inwardstarttime Nullable(String), inwardstarttimeld Nullable(String), rssi Nullable(Int32),
        vin Nullable(String), can_firmware_ver Nullable(String), offset Nullable(Int32),
        session_embedding Nullable(String), burst_mode Nullable(String), fuel_report Nullable(String),
        can_src Nullable(String), can_sn Nullable(String), engine_status Nullable(String),
        protocol_info Nullable(String), idling_report Nullable(String), tc_recommendation Nullable(String),
        num_frames_out Nullable(String), num_frames_in Nullable(UInt32), num_frames_dms Nullable(UInt32),
        num_alerts Nullable(UInt32), inward_models_processed Array(String),
        outward_models_processed Array(String), dms_models_processed Array(String),
        is_inward_processed Nullable(UInt8), is_dms_processed Nullable(UInt8),
        irled_status Nullable(Int32), irled_states_timestamp Nullable(String),
        irled_states_status Nullable(String), faceImageCaptured Nullable(UInt8),
        obs_filetype Nullable(String), audioEnable Nullable(Int32),
        user_generated_alert Nullable(String), rtc_valid Nullable(String),
        rtc_jump_from Nullable(Int64), rtc_jump_to Nullable(Int64), session_count Nullable(String),
        valid_gps_entries Nullable(UInt32), gps_start_time Nullable(Int64), gps_end_time Nullable(Int64),
        nw_source Nullable(String), sinr Nullable(Float64), nw_recorded_time Nullable(Int64),
        idle Nullable(Int32), obdformat Nullable(String), is_inward_cam_obstructed Nullable(UInt8),
        has_multi_lane UInt8 DEFAULT 0, has_road_boundary_tracks UInt8 DEFAULT 0,
        has_ipc_events UInt8 DEFAULT 0, is_hd_file UInt8 DEFAULT 0,
        inward_vision_processed Nullable(UInt8)
    )
    ENGINE = MergeTree
    PARTITION BY toYYYYMM(ifNull(start_time, toDateTime64(0, 3)))
    ORDER BY (device_id, file_name)
    SETTINGS index_granularity = 8192, allow_nullable_key = 1
"""

CLICKHOUSE_VIDEO_METADATA_DDL = """
    CREATE TABLE IF NOT EXISTS video_metadata
    (
        file_name String, device_id String, start_time Nullable(DateTime64(3)),
        end_time Nullable(DateTime64(3)), seq_no UInt16, valid Nullable(UInt8),
        altitude Nullable(Float32), bearing Nullable(Float32), accuracy Nullable(Float32),
        lat Nullable(Float64), long Nullable(Float64), speed Nullable(Float32),
        raw_timestamp Nullable(UInt64), altitudeMSL Nullable(Float32), timestamp Nullable(DateTime64(3))
    )
    ENGINE = MergeTree
    PARTITION BY toYYYYMM(ifNull(start_time, toDateTime64(0, 3)))
    ORDER BY (device_id, start_time, file_name, seq_no)
    SETTINGS index_granularity = 8192, allow_nullable_key = 1
"""


# Configuration constants
MAX_WORKERS_DEFAULT = int(os.getenv('DP_MAX_WORKERS', '4'))
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # in seconds
BATCH_SIZE = int(os.getenv('DP_BATCH_SIZE', '200'))
INSERT_WORKERS_DEFAULT = int(os.getenv('DP_INSERT_WORKERS', '1'))
S3_READ_CHUNK_SIZE = int(os.getenv('DP_S3_CHUNK_SIZE_MB', '8')) * 1024 * 1024
# Extraction is much faster than insertion, so both hand-off points are bounded:
# at most DP_URL_INFLIGHT_FACTOR * max_workers URLs may hold an unconsumed result,
# and at most DP_INSERT_QUEUE_DEPTH extracted chunks may wait on the insert workers.
# Without these the whole window's extracted rows pile up in RAM.
URL_INFLIGHT_FACTOR = max(1, int(os.getenv('DP_URL_INFLIGHT_FACTOR', '2')))
INSERT_QUEUE_DEPTH = max(1, int(os.getenv('DP_INSERT_QUEUE_DEPTH', '4')))
# How many URL results to consume between malloc_trim() calls (0 disables trimming).
MEMORY_TRIM_INTERVAL = max(0, int(os.getenv('DP_MEMORY_TRIM_INTERVAL', '200')))
# A session carries one video_metadata row per GPS sample, so those outnumber
# observation rows by two orders of magnitude and are ~95% of a buffered batch.
# Batches are therefore capped on GPS samples as well as on observation rows;
# BATCH_SIZE alone says nothing about how much memory a batch actually holds.
#
# 100k is deliberately large. The driver's per-row serialization overhead falls as
# the block grows, and each insert is one more MergeTree part for the server to
# merge: a day's poll of ~8.7M rows is 87 inserts at this size versus 435 at 20k.
# The driver also claims a one-time ~78 MiB of arena that malloc_trim cannot
# reclaim, but it plateaus there and does not grow per insert at any batch size.
# Costs ~40 MiB of tuples per buffered batch.
VIDEO_METADATA_BATCH_SIZE = max(1, int(os.getenv('DP_VIDEO_METADATA_BATCH_SIZE', '100000')))

# Initialize logger
logger = Logger('data_processor')


def _process_rss_mb() -> Optional[float]:
    """Resident set size of this process in MiB, or None if unavailable."""
    try:
        with open('/proc/self/statm', 'r') as statm:
            resident_pages = int(statm.read().split()[1])
        return resident_pages * os.sysconf('SC_PAGE_SIZE') / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return None


def _release_freed_memory() -> None:
    """Collect garbage and hand the freed heap back to the OS.

    gc.collect() alone only makes memory reusable inside this process; glibc keeps
    it on per-arena free lists, which is why RSS stayed at its peak long after the
    inserts had finished. malloc_trim() releases those arenas. Also export
    MALLOC_ARENA_MAX=2 before starting the process so each worker thread does not
    grow an arena of its own.
    """
    before = _process_rss_mb()
    gc.collect()
    try:
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except Exception:
        # Non-glibc platform; the gc.collect() above is all we can do.
        pass

    after = _process_rss_mb()
    if before is not None and after is not None:
        # Logged so a run can be checked for the expected plateau: RSS should level
        # off and stay level. Steady linear growth across these lines means
        # something is still being retained.
        logger.log_info(f"RSS {before:.0f} MiB -> {after:.0f} MiB after releasing memory")


def _serialize_json_columns(row: Dict) -> None:
    """Collapse the dict/list JSON columns of one extracted row into strings.

    These columns are inserted as strings anyway, and a row waits in memory between
    extraction and insertion. Serializing here rather than at insert time keeps the
    row several times smaller and drops its references into the parsed source JSON,
    so an archive's object graph can be freed as soon as its file is done.
    """
    for col in CLICKHOUSE_JSON_COLUMNS:
        val = row.get(col)
        if val is None or isinstance(val, str):
            continue
        row[col] = json.dumps(val, separators=(',', ':'))


@dataclass
class ProcessingMetrics:
    """Metrics for monitoring processing performance"""
    total_devices: int = 0
    successful_devices: int = 0
    skipped_devices: int = 0
    failed_devices: int = 0
    total_files: int = 0
    successful_files: int = 0
    failed_files: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'total_devices': self.total_devices,
            'successful_devices': self.successful_devices,
            'skipped_devices': self.skipped_devices,
            'failed_devices': self.failed_devices,
            'total_files': self.total_files,
            'successful_files': self.successful_files,
            'failed_files': self.failed_files,
            'duration_seconds': (self.end_time - self.start_time).total_seconds() if self.start_time and self.end_time else None
        }

class DataProcessor:
    
    def __init__(self, s3_manager, trigger_hash: str, observation_only: bool = False,
                 product_line: Optional[str] = None):
        self.s3_manager = s3_manager
        self.trigger_hash = trigger_hash
        self.observation_only = observation_only or os.getenv('DP_OBSERVATION_ONLY', '0') == '1'
        # Supplied by the caller, or picked up from the device list's product_line
        # column in process_data. It is a path segment of the CSV that was read
        # (OUTPUT/<product_line>/<version>/polling/<date>), never a field in the
        # archive JSON, so it cannot be extracted like the rest of the row.
        self.product_line = product_line
        self.env = None
        self.shared_s3_client = None
        self._thread_lock = threading.Lock()
        self._clickhouse_lock = threading.Lock()
        self._observation_column_types = {}
        self._last_progress_time = time.time()
        self.metrics = ProcessingMetrics()

        # Initialize database connections with proper error handling
        self._init_database_connections()

    def _init_database_connections(self) -> None:
        """Initialize database connections with proper error handling"""
        try:
            # local_conn_pool backs the extracteddata_registry polling-window tracker
            # (see _update_registry_data) and obs_conn_pool discovers pending S3 paths
            # per device (see process_data) -- both are independent of where the
            # extracted rows themselves are written.
            self.local_conn_pool = db_connect_pool('POLL_USER_DB')
            self.obs_conn_pool = None
            self.ch_client = None

            self._init_clickhouse_client()
            self._ensure_clickhouse_schema()

            logger.log_info("Database connections initialized successfully")

        except Exception as e:
            logger.log_error(f"Failed to initialize database connections: {e}")
            raise

    def _init_clickhouse_client(self) -> None:
        """Create the ClickHouse client used for observation_data/video_metadata inserts."""
        parser = configparser.ConfigParser()
        parser.read(CLICKHOUSE_DB_CONFIG_PATH)
        if not parser.has_section(CLICKHOUSE_CONFIG_SECTION):
            raise ValueError(
                f"Section '{CLICKHOUSE_CONFIG_SECTION}' not found in {CLICKHOUSE_DB_CONFIG_PATH}"
            )

        host = parser.get(CLICKHOUSE_CONFIG_SECTION, 'host', fallback='127.0.0.1')
        port = parser.getint(CLICKHOUSE_CONFIG_SECTION, 'port', fallback=9000)
        user = parser.get(CLICKHOUSE_CONFIG_SECTION, 'user', fallback='default')
        password = parser.get(CLICKHOUSE_CONFIG_SECTION, 'password', fallback='')
        database = parser.get(CLICKHOUSE_CONFIG_SECTION, 'database', fallback='default')
        override_host = os.getenv(f'{CLICKHOUSE_CONFIG_SECTION}_HOST', '').strip()
        global_override = os.getenv('DB_HOST_OVERRIDE', '').strip()
        if override_host or global_override:
            host = override_host or global_override

        if host == 'host.docker.internal':
            try:
                socket.gethostbyname(host)
            except OSError:
                fallback_host = os.getenv('DB_DOCKER_HOST_FALLBACK', '127.0.0.1').strip() or '127.0.0.1'
                logger.log_warning(
                    'host.docker.internal is not resolvable on this host; '
                    f'using fallback {fallback_host} for section {CLICKHOUSE_CONFIG_SECTION}. '
                    f'Set {CLICKHOUSE_CONFIG_SECTION}_HOST or DB_HOST_OVERRIDE to control this explicitly.'
                )
                host = fallback_host
        # clickhouse_connect speaks HTTP; the native port (9000) has no HTTP listener.
        http_port = 8123 if port == 9000 else port

        self.ch_client = clickhouse_connect.get_client(
            host=host,
            port=http_port,
            username=user,
            password=password,
            database=database,
        )

    def _ensure_clickhouse_schema(self) -> None:
        """Create observation_data/video_metadata in ClickHouse if they don't exist yet."""
        self.ch_client.command(CLICKHOUSE_OBSERVATION_DATA_DDL)
        self.ch_client.command(CLICKHOUSE_VIDEO_METADATA_DDL)
        self._observation_column_types = self._load_clickhouse_column_types('observation_data')
        logger.log_info("Schema guard check complete for ClickHouse observation_data/video_metadata")

    def _load_clickhouse_column_types(self, table_name: str) -> Dict[str, str]:
        rows = self.ch_client.query(
            "SELECT name, type FROM system.columns WHERE database = currentDatabase() AND table = %(table)s",
            parameters={'table': table_name},
        ).result_rows
        return {name: col_type for name, col_type in rows}

    @staticmethod
    def _base_clickhouse_type(col_type: str) -> str:
        while col_type.startswith('Nullable(') and col_type.endswith(')'):
            col_type = col_type[len('Nullable('):-1]
        return col_type

    def _coerce_clickhouse_value(self, col: str, val: Any) -> Any:
        if val is None:
            return None

        col_type = self._base_clickhouse_type(self._observation_column_types.get(col, ''))
        if not col_type:
            return val

        if col_type.startswith('Array('):
            return val if isinstance(val, list) else []
        if col_type.startswith('String') or col_type.startswith('FixedString'):
            return str(val)
        if col_type.startswith('UInt8') and isinstance(val, bool):
            return int(val)
        return val

    @contextmanager
    def _get_s3_client(self):
        """Thread-safe shared S3 client context manager.

        Reusing one client per process avoids repeated credential-provider and IMDS lookups
        when running on EC2 with many worker threads.
        """
        if self.shared_s3_client is None:
            with self._thread_lock:
                if self.shared_s3_client is None:
                    try:
                        self.shared_s3_client = self.s3_manager.get_s3_client()
                    except Exception as e:
                        logger.log_error(f"Failed to create shared S3 client: {e}")
                        raise

        yield self.shared_s3_client

    def epoch_to_utc(self, epoch_time: Optional[int]) -> Optional[str]:
        """Convert epoch timestamp to UTC string with validation"""
        if epoch_time is None:
            return None
        
        try:
            # Validate epoch time range (reasonable years 1970-2100)
            if epoch_time < 0 or epoch_time > 4102444800000:  # Year 2100
                logger.log_warning(f"Invalid epoch time: {epoch_time}")
                return None
                
            return datetime.fromtimestamp(epoch_time / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError, OSError) as e:
            logger.log_error(f"Error converting epoch time {epoch_time}: {e}")
            return None

    def extract_timestamp_from_filename(self, file_name: Optional[str]) -> Optional[int]:
        """Extract timestamp from filename with improved error handling"""
        if not file_name:
            return None
            
        try:
            match = re.search(r'_(\d{13})_', file_name)
            if match:
                timestamp = int(match.group(1))
                # Validate timestamp range
                if timestamp < 0 or timestamp > 4102444800000:
                    logger.log_warning(f"Invalid timestamp in filename {file_name}: {timestamp}")
                    return None
                return timestamp
            else:
                logger.log_debug(f"No timestamp pattern found in filename: {file_name}")
                return None
        except (ValueError, AttributeError) as e:
            logger.log_error(f"Error extracting timestamp from filename {file_name}: {e}")
            return None

    def _retry_operation(self, operation, *args, **kwargs):
        """Retry operation with exponential backoff"""
        for attempt in range(MAX_RETRIES):
            try:
                return operation(*args, **kwargs)
            except Exception as e:
                error_code = getattr(getattr(e, 'response', {}), 'get', lambda *_: None)('Error', {}).get('Code') if hasattr(e, 'response') else None
                if error_code in {'NoSuchKey', 'NoSuchBucket', '404'}:
                    logger.log_warning(f"Skipping non-retryable S3 error: {e}")
                    raise e

                if attempt == MAX_RETRIES - 1:
                    raise e
                
                delay = RETRY_DELAY * (2 ** attempt)
                logger.log_warning(f"Operation failed (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {delay}s: {e}")
                time.sleep(delay)

    def process_url(self, device_id: str, url: str, date_range: List[Dict]) -> Tuple[str, List[Dict], List[Dict]]:
        """Process S3 URL with improved error handling and monitoring"""
        start_time = time.time()
        
        try:
            with self._get_s3_client() as s3_client:
                logger.log_debug(f"Processing URL: {url} for device_id: {device_id}")
                
                # Parse URL
                parsed_url = urlparse(url)
                if not parsed_url.netloc or not parsed_url.path:
                    raise ValueError(f"Invalid URL format: {url}")
                
                bucket_name = parsed_url.netloc.split('.')[0]
                file_key = parsed_url.path.lstrip('/')
                product_line = self.product_line
                
                # Stream download to a temp file to avoid large in-memory buffers on EC2.
                def process_archive_from_s3() -> List[Dict]:
                    extracted_data_local: List[Dict] = []
                    with tempfile.NamedTemporaryFile(suffix='.7z', delete=True) as tmp_file:
                        response = s3_client.get_object(Bucket=bucket_name, Key=file_key)
                        body = response['Body']
                        try:
                            for chunk in body.iter_chunks(chunk_size=S3_READ_CHUNK_SIZE):
                                if chunk:
                                    tmp_file.write(chunk)
                        finally:
                            body.close()

                        tmp_file.flush()

                        with tempfile.TemporaryDirectory() as extract_dir:
                            with py7zr.SevenZipFile(tmp_file.name, mode='r') as archive:
                                json_names = [name for name in archive.getnames() if name.endswith('.json')]
                                if json_names:
                                    archive.extract(path=extract_dir, targets=json_names)

                            for name in json_names:
                                extracted_path = os.path.join(extract_dir, name)
                                if not os.path.exists(extracted_path):
                                    continue

                                data = None
                                try:
                                    with open(extracted_path, 'r', encoding='utf-8') as json_file:
                                        data = json.load(json_file)
                                    extracted_item = self._extract_data(data, url, self.trigger_hash, product_line)
                                    # The extracted row holds no references into `data`
                                    # (_serialize_json_columns collapsed the nested columns
                                    # to strings), so drop the parsed document now rather
                                    # than keeping it alive while the next file is parsed.
                                    data = None
                                    if extracted_item:
                                        extracted_data_local.append(extracted_item)
                                    extracted_item = None
                                except json.JSONDecodeError as e:
                                    logger.log_error(f"Invalid JSON in file {name} from {url}: {e}")
                                except Exception as e:
                                    logger.log_error(f"Error processing file {name} from {url}: {e}")
                                finally:
                                    data = None

                    return extracted_data_local

                extracted_data = self._retry_operation(process_archive_from_s3)
                
                processing_time = time.time() - start_time
                logger.log_debug(f"Successfully processed {url} in {processing_time:.2f}s, extracted {len(extracted_data)} records")
                
                self.metrics.successful_files += 1
                return device_id, extracted_data, date_range
                
        except Exception as e:
            processing_time = time.time() - start_time
            logger.log_error(f"Error processing URL {url} for device {device_id} after {processing_time:.2f}s: {e}")
            self.metrics.failed_files += 1
            return device_id, [], date_range

    def _extract_data(self, data: Dict, url: str, trigger_hash: str, product_line: Optional[str] = None) -> Optional[Dict]:
        """Extract data from JSON with comprehensive validation"""
        try:
            if not isinstance(data, dict):
                logger.log_warning(f"Invalid data type, expected dict, got {type(data)}")
                return None
            
            device_id = data.get('deviceId')
            if not device_id:
                logger.log_warning("Missing deviceId in data")
                return None
            
            logger.log_debug(f"Extracting data for device_id: {device_id}")
            
            # Extract video metadata with error handling
            video_metadata = data.get('videoMetaData', [])
            speeds = []
            if isinstance(video_metadata, list):
                for item in video_metadata:
                    if isinstance(item, dict) and 'speed' in item:
                        try:
                            speed = float(item['speed'])
                            if 0 <= speed <= 300:  # Reasonable speed range
                                speeds.append(speed)
                        except (ValueError, TypeError):
                            continue
            
            # Extract driver ID safely
            driver_id = None
            driver_id_list = data.get('driverId')
            if isinstance(driver_id_list, list) and driver_id_list:
                try:
                    driver_id = str(driver_id_list[0]).split(': ')[-1] if driver_id_list[0] else None
                except (IndexError, AttributeError):
                    pass
            
            # Build extracted data
            file_name = data.get('videoName')
            file_timestamp = self.extract_timestamp_from_filename(file_name) if file_name else None

            if self.observation_only:
                return self._extract_observation_data(
                    data,
                    url,
                    file_name,
                    file_timestamp,
                    device_id,
                    product_line,
                )

            # Keys to exclude from model processing lists
            EXCLUDED_INWARD_KEYS = {'annotation_image_scale','annotation_driver_side','process_fps','drop_packet','nrt_code_version','frame_rate','annotation_start_idx','frames_processed_info','process_rate','annotation_driving_side'}
            EXCLUDED_OUTWARD_KEYS = {'peak_intensity', 'locale', 'frameRate', 'highg_thread_retries', 'localSpeedUnit', 'videoDelay', 'splitVideoMode', 'dnnSubsampleFactor', 'lowg_thread_retries', 'module', 'highg_classification_enabled', 'lowg_classification_enabled', 'numFrames', 'laneCalSuccess', 'objTrackerMinTrackLength'}
            EXCLUDED_DMS_KEYS = {'nrt_code_version', 'privacy', 'frame_rate', 'process_fps', 'drop_packet', 'frames_processed_info', 'process_rate'}
            
            extracted_data = {
                'ota': data.get('app_ver'),
                'product_line': product_line,
                'udid': data.get('udid'),
                'file_name': file_name,
                'file_timestamp': file_timestamp,
                'start_time': self.epoch_to_utc(data.get('startTime')),
                'end_time': self.epoch_to_utc(data.get('endTime')),
                'ignition_status': data.get('deviceModes', {}).get('ignition_status'),
                'uptime': data.get('systemUpTime'),
                'service_uptime': data.get('serviceUpTime'),
                'privacymode': data.get('deviceModes', {}).get('privacymode'),
                'dismode': data.get('deviceModes', {}).get('disMode'),
                'voltage': data.get('Voltage in Volts'),
                'processing_mode': data.get('deviceModes', {}).get('processing_mode'),
                'inertial_processed': self._check_module_processed(data, 'inertial'),
                'vision_processed': self._check_module_processed(data, 'vision'),
                'nrt_status': data.get('NRT_STATUS'),
                'tripno': data.get('tripNo'),
                'videometadatastatus': len(video_metadata) if isinstance(video_metadata, list) else 0,
                'min_speed': min(speeds) if speeds else None,
                'max_speed': max(speeds) if speeds else None,
                'sensormetadata_count': len(data.get('sensorMetaData', [])) if isinstance(data.get('sensorMetaData'), list) else 0,
                'driverinvariantsession': data.get('driverInvariantSession'),
                'driverid': driver_id,
                'vehclass': data.get('vehClass'),
                'vehicleid': data.get('vehicleId'),
                'cameras': data.get('cameras'),
                'prevvideoname': data.get('prevVideoName'),
                'current_videoname': data.get('videoName'),
                'nextvideoname': data.get('nextVideoName'),
                'devicemodes_itemscount': len(data.get('deviceModes', {})) if isinstance(data.get('deviceModes'), dict) else 0,
                'inference_data_itemscount': len(data.get('inference_data', {})) if isinstance(data.get('inference_data'), dict) else 0,
                'canmetadata': data.get('inference_data', {}).get('observations_data', {}).get('canMetaData', []),
                'alerts_data_num_alerts': data.get('inference_data', {}).get('alerts_data', {}).get('num_alerts'),
                'alerts_data': self._extract_alerts_data(data.get('inference_data', {}).get('alerts_data', {})),
                'audio_events_num_alerts': data.get('inference_data', {}).get('audio_events', {}).get('num_alerts'),
                'audio_events_data': self._extract_audio_events_data(data.get('inference_data', {}).get('audio_events', {})),
                'events_data_num_alerts': data.get('inference_data', {}).get('events_data', {}).get('num_alerts'),
                'events_data': self._extract_events_data(data.get('inference_data', {}).get('events_data', {})),
                'metadatastatus': data.get('metadataStatus', 'full'),
                'device_id': device_id,
                's3_path': url,
                # Per-sample speeds live in video_metadata.speed; only the aggregates
                # (min_speed/max_speed/videometadatastatus) belong in observation_data.
                'speed_data': None,
                'starttime': data.get('startTime'),
                'starttimeld': data.get('startTimeLd'),
                'inwardstarttime': data.get('inwardStartTime'),
                'inwardstarttimeld': data.get('inwardStartTimeLd'),
                'rssi': data.get('networkInfo', {}).get('rssi'),
                'vin': data.get('vin'),
                'can_firmware_ver': data.get('can_firmware_ver'),
                'offset': data.get('offset'),
                'session_embedding': data.get('session_embedding', {}),
                'burst_mode': data.get('burst_mode', {}),
                'fuel_report': data.get('fuel_report', {}),
                'can_src': data.get('can_src'),
                'can_sn':data.get('can_sn', None),
                'engine_status': data.get('engine_status'),
                'protocol_info': data.get('inference_data', {}).get('observations_data', {}).get('protocol_info'),
                'idling_report': data.get('inference_data', {}).get('observations_data', {}).get('idling_report', {}),
                'tc_recommendation':data.get('inference_data',{}).get('video_data', {}).get('transcode_recommendation'),
                'num_frames_out': data.get('inference_data', {}).get('observations_data', {}).get('numFrames', None),
                'num_frames_in': data.get('inference_data', {}).get('inward', {}).get('frames_processed_info', {}).get('num_frames_processed', None),
                'num_frames_dms': data.get('inference_data', {}).get('dms', {}).get('frames_processed_info', {}).get('num_frames_processed', None),
                'num_alerts': data.get('inference_data', {}).get('events_data', {}).get('num_alerts', None),
                'inward_models_processed': list(set(data.get('inference_data', {}).get('inward', {}).keys()) - EXCLUDED_INWARD_KEYS),
                'outward_models_processed': list(set(data.get('inference_data', {}).get('observations_data', {}).keys()) - EXCLUDED_OUTWARD_KEYS),
                'dms_models_processed': list(set(data.get('inference_data', {}).get('dms', {}).keys()) - EXCLUDED_DMS_KEYS),
                'is_inward_processed': data.get('inference_data', {}).get('is_inward_processed_on_device', None),
                'is_dms_processed': data.get('inference_data', {}).get('is_dms_processed_on_device', None),
                'irled_status': data.get("irled_status", None),
                'irled_states_timestamp': ','.join(str(item.get('time')) for item in data.get('irled_states', []) if item.get('time') is not None),
                'irled_states_status': ','.join(str(item.get('status')) for item in data.get('irled_states', []) if item.get('status') is not None),
                'faceImageCaptured': data.get('inference_data', {}).get('faceImageCaptured', None),
                'obs_filetype': 'Observation' if 'observations_data' in data.get('inference_data', {}) else 'Metadata',
                'audioEnable': data.get('audioEnable'),
                'user_generated_alert': data.get('user_generated_alert', []),
                'rtc_valid': data.get('rtcValid'),
                'rtc_jump_from': data.get('rtc_jump_from'),
                'rtc_jump_to': data.get('rtc_jump_to'),
                'session_count': data.get('sessionCount'),
                'valid_gps_entries': data.get('validGPSEntries'),
                'gps_start_time': data.get('gpsStartTime'),
                'gps_end_time': data.get('gpsEndTime'),
                'nw_source': data.get('networkInfo', {}).get('rat'),
                'sinr': data.get('networkInfo', {}).get('sinr'),
                'nw_recorded_time': data.get('networkInfo', {}).get('recordedTime'),
                'idle': data.get('deviceModes', {}).get('idle'),
                'obdformat': data.get('obdFormat'),
                'is_inward_cam_obstructed': bool(data.get('inference_data', {}).get('is_inward_cam_obstructed')) if data.get('inference_data', {}).get('is_inward_cam_obstructed') is not None else None,
                'has_multi_lane': 'multiLane' in data.get('inference_data', {}).get('observations_data', {}),
                'has_road_boundary_tracks': 'roadBoundaryTracks' in data.get('inference_data', {}).get('observations_data', {}),
                'has_ipc_events': 'ipc_events' in data.get('inference_data', {}).get('inward', {}),
                'is_hd_file': 'carBoxTrackerListCompressed' in data.get('inference_data', {}).get('observations_data', {}),
                'inward_vision_processed': self._check_module_processed(data, 'inward_vision'),
            }

            _serialize_json_columns(extracted_data)
            extracted_data['_video_metadata_rows'] = self._build_video_metadata_rows(
                video_metadata, file_name, device_id,
                extracted_data['start_time'], extracted_data['end_time'],
            )

            return extracted_data

        except Exception as e:
            logger.log_error(f"Error extracting data: {e}")
            return None

    def _build_video_metadata_rows(
        self,
        video_metadata: Any,
        file_name: Optional[str],
        device_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
    ) -> List[Tuple]:
        """Flatten a file's videoMetaData[] array into video_metadata table rows.

        Rows are tuples ordered like VIDEO_METADATA_COLUMNS rather than dicts: a
        session has one entry per GPS sample, so these dominate a buffered row's
        footprint and a 15-field dict costs several times what the tuple does.
        """
        if not isinstance(video_metadata, list):
            return []

        rows = []
        for seq_no, item in enumerate(video_metadata):
            if not isinstance(item, dict):
                continue
            rows.append((
                file_name,
                device_id,
                start_time,
                end_time,
                seq_no,
                item.get('valid'),
                item.get('altitude'),
                item.get('bearing'),
                item.get('accuracy'),
                item.get('lat'),
                item.get('long'),
                item.get('speed'),
                item.get('raw_timestamp'),
                item.get('altitudeMSL'),
                self.epoch_to_utc(item.get('timestamp')),
            ))
        return rows

    def _extract_observation_data(
        self,
        data: Dict,
        url: str,
        file_name: Optional[str],
        file_timestamp: Optional[int],
        device_id: str,
        product_line: Optional[str] = None,
    ) -> Dict:
        """Fast-path extraction for observation-oriented fields only."""
        inference_data = data.get('inference_data', {})
        observations = inference_data.get('observations_data', {}) if isinstance(inference_data, dict) else {}
        video_metadata = data.get('videoMetaData', [])
        start_time_str = self.epoch_to_utc(data.get('startTime'))
        end_time_str = self.epoch_to_utc(data.get('endTime'))

        row = {
            'ota': data.get('app_ver'),
            'product_line': product_line,
            'device_id': device_id,
            'udid': data.get('udid'),
            'file_name': file_name,
            'file_timestamp': file_timestamp,
            'start_time': start_time_str,
            'end_time': end_time_str,
            'starttime': data.get('startTime'),
            'starttimeld': data.get('startTimeLd'),
            's3_path': url,
            'canmetadata': observations.get('canMetaData', []) if isinstance(observations, dict) else [],
            'protocol_info': observations.get('protocol_info') if isinstance(observations, dict) else None,
            'idling_report': observations.get('idling_report', {}) if isinstance(observations, dict) else {},
            'num_frames_out': observations.get('numFrames') if isinstance(observations, dict) else None,
            'outward_models_processed': list(set(observations.keys()) - {
                'peak_intensity', 'locale', 'frameRate', 'highg_thread_retries', 'localSpeedUnit',
                'videoDelay', 'splitVideoMode', 'dnnSubsampleFactor', 'lowg_thread_retries', 'module',
                'highg_classification_enabled', 'lowg_classification_enabled', 'numFrames',
                'laneCalSuccess', 'objTrackerMinTrackLength'
            }) if isinstance(observations, dict) else [],
            'inertial_processed': self._check_module_processed(data, 'inertial'),
            'vision_processed': self._check_module_processed(data, 'vision'),
            'metadatastatus': data.get('metadataStatus', 'full'),
            'tripno': data.get('tripNo'),
            'vehicleid': data.get('vehicleId'),
            'vin': data.get('vin'),
            'session_embedding': data.get('session_embedding', {}),
            'burst_mode': data.get('burst_mode', {}),
            'fuel_report': data.get('fuel_report', {}),
        }
        _serialize_json_columns(row)
        row['_video_metadata_rows'] = self._build_video_metadata_rows(
            video_metadata, file_name, device_id, start_time_str, end_time_str,
        )
        return row

    def _bulk_insert_rows(self, rows: List[Dict]) -> None:
        """Split each extracted row into its observation_data and video_metadata
        parts, then insert both into ClickHouse.

        Takes ownership of `rows`: the list is drained and the rows are mutated in
        place, so a batch is never duplicated just to be split. Callers must not
        reuse the list afterwards.
        """
        if not rows:
            return

        video_metadata_rows: List[Tuple] = []
        for row in rows:
            video_metadata_rows.extend(row.pop('_video_metadata_rows', None) or [])

        self._insert_clickhouse_observation_rows(rows)
        self._insert_clickhouse_video_metadata_rows(video_metadata_rows)

    def _prepare_clickhouse_observation_row(self, row: Dict) -> Dict:
        """Coerce one extracted-data dict into ClickHouse-ready column values."""
        prepared = {}
        for col, val in row.items():
            if col in CLICKHOUSE_JSON_COLUMNS:
                # Usually already a string: _serialize_json_columns() collapses these
                # at extraction time. The dict/list branch stays for direct callers.
                if val is None or val == '':
                    prepared[col] = None
                elif isinstance(val, str):
                    prepared[col] = val
                else:
                    prepared[col] = json.dumps(val, separators=(',', ':'))
            elif col in CLICKHOUSE_BOOL_COLUMNS:
                prepared[col] = None if val is None else int(bool(val))
            elif col in CLICKHOUSE_ARRAY_COLUMNS:
                prepared[col] = val if isinstance(val, list) else []
            elif col in CLICKHOUSE_TEXT_COLUMNS:
                prepared[col] = None if val is None else str(val)
            else:
                prepared[col] = self._coerce_clickhouse_value(col, val)
        return prepared

    def _insert_clickhouse_observation_rows(self, rows: List[Dict]) -> None:
        """Insert extracted rows, draining `rows` as it goes.

        Each row is converted and released one at a time instead of materializing a
        prepared-dict list and then a value-matrix on top of the originals; that kept
        three copies of every batch alive at once.
        """
        if not rows:
            return

        columns = list(rows[0].keys())
        data = []
        while rows:
            prepared = self._prepare_clickhouse_observation_row(rows.pop())
            data.append([prepared.get(col) for col in columns])

        with self._clickhouse_lock:
            self.ch_client.insert('observation_data', data, column_names=columns)
        logger.log_info(f"Inserted {len(data)} row(s) into ClickHouse observation_data")

    def _insert_clickhouse_video_metadata_rows(self, rows: List[Tuple]) -> None:
        """Insert video_metadata rows already ordered like VIDEO_METADATA_COLUMNS."""
        if not rows:
            return

        row_count = len(rows)
        with self._clickhouse_lock:
            self.ch_client.insert('video_metadata', rows, column_names=VIDEO_METADATA_COLUMNS)
        logger.log_info(f"Inserted {row_count} row(s) into ClickHouse video_metadata")

    def _check_module_processed(self, data: Dict, module_type: str) -> bool:
        """Check if a specific module type was processed"""
        try:
            modules = data.get('inference_data', {}).get('observations_data', {}).get('module', [])
            if isinstance(modules, list):
                return any(module_type in str(module).lower() for module in modules)
            return False
        except (AttributeError, TypeError):
            return False

    def _extract_alerts_data(self, alerts_data: Dict) -> List[Dict]:
        """Extract alerts data with validation"""
        try:
            alerts = alerts_data.get('alerts', [])
            if isinstance(alerts, list):
                return [{'uuid': alert.get('uuid')} for alert in alerts if isinstance(alert, dict)]
            return []
        except (AttributeError, TypeError):
            return []

    def _extract_audio_events_data(self, audio_events: Dict) -> List[Dict]:
        """Extract audio events data with validation"""
        try:
            alerts = audio_events.get('alerts', [])
            if isinstance(alerts, list):
                return [
                    {
                        'uuid': alert.get('uuid'),
                        'event_code': alert.get('event_code'),
                        'playback_reason': alert.get('reason'),
                        'playback_success': alert.get('playback_success')
                    }
                    for alert in alerts if isinstance(alert, dict)
                ]
            return []
        except (AttributeError, TypeError):
            return []

    def _extract_events_data(self, events_data: Dict) -> List[Dict]:
        """Extract events data with validation"""
        try:
            alerts = events_data.get('alerts', [])
            if isinstance(alerts, list):
                return [
                    {
                        'uuid': alert.get('uuid'),
                        'event_code': alert.get('event_code'),
                        'description': alert.get('description'),
                        'event_initialization_time': alert.get('event_initialization_time')
                    }
                    for alert in alerts if isinstance(alert, dict)
                ]
            return []
        except (AttributeError, TypeError):
            return []

    def _log_progress(self, current: int, total: int, operation: str) -> None:
        """Log progress at intervals"""
        current_time = time.time()
        if current_time - self._last_progress_time >= 30:  # Log every 30 seconds
            percentage = (current / total * 100) if total > 0 else 0
            logger.log_info(f"{operation}: {current}/{total} ({percentage:.1f}%) completed")
            self._last_progress_time = current_time

    def _resolve_product_line(self, sample_device_info_df: pd.DataFrame) -> None:
        """Adopt the device list's product_line so it can be written to every row.

        obs_processor derives it from the CSV's own path and puts it on the frame; a
        constructor-supplied value wins. One CSV covers one product line, so a frame
        carrying several means the wrong file was handed in and each row would be
        labelled from whichever value came first -- warn instead of guessing.
        """
        if self.product_line is not None:
            logger.log_info(f"Product line for this run: {self.product_line} (supplied by caller)")
            return

        if 'product_line' not in sample_device_info_df.columns:
            logger.log_warning(
                "Device list has no product_line column; observation_data.product_line "
                "will be NULL for this run"
            )
            return

        values = [str(v).strip() for v in sample_device_info_df['product_line'].dropna().unique()
                  if str(v).strip() and str(v).strip().lower() != 'nan']
        if not values:
            logger.log_warning(
                "Device list product_line column is empty; observation_data.product_line "
                "will be NULL for this run"
            )
            return

        if len(values) > 1:
            logger.log_warning(
                f"Device list carries multiple product lines {values}; using {values[0]}"
            )

        self.product_line = values[0]
        logger.log_info(f"Product line for this run: {self.product_line} (from device list)")

    def process_data(self, sample_device_info_df: pd.DataFrame) -> Dict:
        """Process data with comprehensive monitoring and error handling"""
        self.metrics.start_time = datetime.now()
        self.metrics.total_devices = len(sample_device_info_df)
        
        logger.log_info(f"Starting data processing for {self.metrics.total_devices} devices")
        
        try:
            # Validate input
            if sample_device_info_df.empty:
                logger.log_warning("No devices to process")
                return {}
            
            # Initialize environment
            self.env = sample_device_info_df['environment'].unique()[0]
            logger.log_info(f"Environment detected: {self.env}")

            self._resolve_product_line(sample_device_info_df)

            # Warm up shared client once to reduce first-request auth latency per run.
            if os.getenv('DP_PREWARM_S3_CLIENT', '1') == '1':
                with self._get_s3_client():
                    pass
            
            # Initialize observation database connection
            if self.obs_conn_pool is None:
                db_name = 'PROD_OBS_DB' if self.env == 'production' else 'STAG_OBS_DB'
                self.obs_conn_pool = db_connect_pool(db_name)
                logger.log_info(f"Connected to {db_name}")

            s3_dict = {}
            max_workers = min(os.cpu_count() or 4, MAX_WORKERS_DEFAULT)
            
            logger.log_info(f"Using {max_workers} worker threads for S3 path fetching")

            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="S3Fetch") as executor:
                # Submit all tasks
                futures = []
                for idx, row in sample_device_info_df.iterrows():
                    future = executor.submit(self.s3_manager.fetch_s3_path, row, self.obs_conn_pool, self.local_conn_pool)
                    futures.append(future)
                
                # Process completed tasks
                completed_count = 0
                for future in as_completed(futures):
                    completed_count += 1
                    self._log_progress(completed_count, len(futures), "S3 path fetching")
                    
                    try:
                        result = future.result()
                        if result:
                            s3_dict.update(result)
                            self.metrics.successful_devices += len(result)
                        else:
                            self.metrics.skipped_devices += 1
                            
                    except Exception as e:
                        self.metrics.failed_devices += 1
                        logger.log_error(f"S3 path fetch task failed: {e}")
                        
            logger.log_info(
                "S3 path fetching completed. "
                f"Successful: {self.metrics.successful_devices}, "
                f"Skipped(no pending/no URLs): {self.metrics.skipped_devices}, "
                f"Failed(errors): {self.metrics.failed_devices}"
            )
            return s3_dict
            
        except Exception as e:
            logger.log_error(f"Critical error in process_data: {e}")
            raise
        finally:
            self.metrics.end_time = datetime.now()

    def _drain_url_results(self, done, batch_queue, batch_size: int) -> int:
        """Hand each finished URL's rows to the insert queue, releasing as we go.

        A completed Future keeps its whole result list reachable, so futures are
        popped out of `done` and dropped one at a time; keeping every completed
        future in a list (the previous shape) pinned every row extracted during the
        run. Chunks are taken off the tail of the result so the extracted list
        shrinks while it is being handed over, and batch_queue.put() blocks once the
        insert workers fall behind, which is what bounds peak memory.
        """
        consumed = 0
        while done:
            future = done.pop()
            consumed += 1
            try:
                device_id, extracted_data, _ = future.result()
            except Exception as e:
                logger.log_error(f"URL processing failed: {e}")
                continue
            finally:
                future = None

            while extracted_data:
                # Cap a chunk on both observation rows and the GPS samples they
                # carry, so one session with a long trip cannot make a batch huge.
                chunk = []
                samples = 0
                while (extracted_data and len(chunk) < batch_size
                       and samples < VIDEO_METADATA_BATCH_SIZE):
                    row = extracted_data.pop()
                    samples += len(row.get('_video_metadata_rows') or ())
                    chunk.append(row)
                batch_queue.put((device_id, chunk))
                chunk = None
            extracted_data = None
        return consumed

    def insert_data_to_db(self, s3_dict: Dict) -> None:
        """Extract each device's S3 archives and stream the rows into ClickHouse.

        Both hand-off points are bounded, because extraction runs far ahead of
        insertion: only `max_inflight` URLs may hold an unconsumed result, and only
        INSERT_QUEUE_DEPTH extracted chunks may wait on the insert workers. When the
        inserts fall behind, back-pressure propagates all the way to the S3 stage
        instead of the backlog accumulating in RAM.
        """
        if not s3_dict:
            logger.log_info("No data to insert into the database")
            return

        start_time = datetime.now()
        logger.log_info(f"Starting database insertion for {len(s3_dict)} devices")

        batch_size = BATCH_SIZE
        insert_workers = max(1, INSERT_WORKERS_DEFAULT)
        max_workers = min(os.cpu_count() or 4, MAX_WORKERS_DEFAULT)
        max_inflight = max(max_workers, max_workers * URL_INFLIGHT_FACTOR)
        device_registry_data = {}

        logger.log_info(
            f"Processing URLs using {max_workers} workers "
            f"(max {max_inflight} results in flight), batch size {batch_size}, "
            f"{insert_workers} insert worker(s), insert queue depth {INSERT_QUEUE_DEPTH}"
        )

        batch_queue: queue.Queue = queue.Queue(maxsize=INSERT_QUEUE_DEPTH)

        def insert_device_batch(device_id: str, rows: list):
            """Insert a batch of data for a device"""
            if not rows:
                return

            row_count = len(rows)
            logger.log_info(f"Inserting batch for device {device_id}, rows={row_count}")

            try:
                self._bulk_insert_rows(rows)

                logger.log_info(f"Successfully inserted batch for device {device_id}")

            except Exception as e:
                error_details = {
                    'timestamp': datetime.now().isoformat(),
                    'device_id': device_id,
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'traceback': traceback.format_exc(),
                    'batch_rows': row_count,
                }

                logger.log_error(
                    f"Database Insertion Error Report:\n"
                    f"{'='*60}\n"
                    f"Timestamp: {error_details['timestamp']}\n"
                    f"Device ID: {error_details['device_id']}\n"
                    f"Error Type: {error_details['error_type']}\n"
                    f"Error Message: {error_details['error_message']}\n"
                    f"Batch Rows: {error_details['batch_rows']}\n"
                    f"{'='*60}\n"
                    f"Full Traceback:\n{error_details['traceback']}"
                )
                raise

        def insert_worker():
            """Drain batch_queue until the producer sends the None sentinel."""
            while True:
                item = batch_queue.get()
                try:
                    if item is None:
                        return
                    device_id, rows = item
                    item = None
                    try:
                        insert_device_batch(device_id, rows)
                    except Exception as e:
                        logger.log_error(f"Batch insertion failed for device {device_id}: {e}")
                    finally:
                        rows = None
                finally:
                    batch_queue.task_done()

        def url_tasks():
            """Yield (device_id, url, date_range) lazily so nothing is pre-submitted."""
            for device_id, data in s3_dict.items():
                urls, date_range = data
                device_registry_data[device_id] = date_range
                for url in urls:
                    yield device_id, url, date_range

        insert_threads = [
            threading.Thread(target=insert_worker, name=f"BatchInsert-{i}", daemon=True)
            for i in range(insert_workers)
        ]
        for thread in insert_threads:
            thread.start()

        try:
            # -----------------------------
            # STEP 1: PROCESS URLS (PARALLEL, BOUNDED)
            # -----------------------------
            consumed = 0
            since_trim = 0
            pending = set()

            try:
                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="URLProcess") as executor:
                    for device_id, url, date_range in url_tasks():
                        if len(pending) >= max_inflight:
                            done, pending = wait(pending, return_when=FIRST_COMPLETED)
                            consumed += self._drain_url_results(done, batch_queue, batch_size)
                            done = None
                            since_trim = self._maybe_trim_memory(consumed, since_trim)
                        pending.add(executor.submit(self.process_url, device_id, url, date_range))

                    while pending:
                        done, pending = wait(pending, return_when=FIRST_COMPLETED)
                        consumed += self._drain_url_results(done, batch_queue, batch_size)
                        done = None
                        since_trim = self._maybe_trim_memory(consumed, since_trim)

                logger.log_info(
                    f"URL processing completed for {consumed} URL(s); waiting for pending inserts"
                )
                batch_queue.join()
            finally:
                for _ in insert_threads:
                    batch_queue.put(None)
                for thread in insert_threads:
                    thread.join(timeout=300)

            s3_dict.clear()
            _release_freed_memory()

            # -----------------------------
            # STEP 2: UPDATE REGISTRY
            # -----------------------------
            self._update_registry_data(device_registry_data)

            duration = (datetime.now() - start_time).total_seconds()
            logger.log_info(f"Database insertion completed in {duration:.2f}s using batch processing")

        except Exception as e:
            logger.log_error(f"Critical error in insert_data_to_db: {e}")
            raise

    @staticmethod
    def _maybe_trim_memory(consumed: int, since_trim: int) -> int:
        """Return freed arenas to the OS every MEMORY_TRIM_INTERVAL URLs."""
        if not MEMORY_TRIM_INTERVAL:
            return since_trim
        if consumed - since_trim < MEMORY_TRIM_INTERVAL:
            return since_trim
        _release_freed_memory()
        return consumed

    def _update_registry_data(self, device_registry_data: Dict[str, List[Dict]]) -> None:
        """Update registry data with proper error handling and OTA details"""
        logger.log_info(f"Updating registry for {len(device_registry_data)} devices")
        
        for device_id, date_ranges in device_registry_data.items():
            if not date_ranges:
                continue
                
            # Remove duplicates and collect OTA information
            unique_date_ranges = []
            seen = set()
            ota_versions = set()  # Collect unique OTA versions for this device
            
            for item in date_ranges:
                if not isinstance(item, dict) or 'sd' not in item or 'ed' not in item:
                    continue
                    
                date_key = (item['sd'], item['ed'])
                if date_key not in seen:
                    seen.add(date_key)
                    unique_date_ranges.append(item)
                    
                    # Collect OTA version if available
                    if 'ota' in item and item['ota']:
                        ota_versions.add(item['ota'])
            
            if not unique_date_ranges:
                logger.log_warning(f"No valid date ranges for device {device_id}")
                continue
            
            # Convert OTA versions set to list for logging/storage
            ota_list = list(ota_versions) if ota_versions else []
            logger.log_debug(f"Device {device_id} has OTA versions: {ota_list}")
            
            loc_conn = None
            try:
                loc_conn = self.local_conn_pool.getconn()
                
                for item in unique_date_ranges:
                    try:
                        # Get OTA version for this specific date range
                        ota_version = item.get('ota', None)
                        
                        # Call update_registry_data with OTA information
                        update_registry_data(
                            "extracteddata_registry", 
                            device_id, 
                            loc_conn, 
                            item['sd'], 
                            item['ed'],
                            ota=ota_version  # Pass OTA version
                        )
                        
                        logger.log_debug(f"Registry updated for device {device_id}, range {item['sd']}-{item['ed']}, OTA: {ota_version}")
                        
                    except Exception as e:
                        logger.log_error(f"Error updating registry for device {device_id}, range {item}, OTA: {item.get('ota', 'N/A')}: {e}")
                        try:
                            loc_conn.rollback()
                        except:
                            pass
                
                logger.log_info(f"Registry updated for device {device_id} with {len(unique_date_ranges)} date ranges and OTA versions: {ota_list}")
                
            except Exception as e:
                logger.log_error(f"Failed to get connection for device {device_id}: {e}")
            finally:
                if loc_conn:
                    try:
                        self.local_conn_pool.putconn(loc_conn)
                    except Exception as e:
                        logger.log_error(f"`Error returning connection to pool: {e}")

    def cleanup(self) -> None:
        """Cleanup resources"""
        try:
            # Close S3 sessions
            with self._thread_lock:
                if self.shared_s3_client and hasattr(self.shared_s3_client, 'close'):
                    try:
                        self.shared_s3_client.close()
                    except Exception as e:
                        logger.log_error(f"Error closing shared S3 client: {e}")
                self.shared_s3_client = None
            
            # Close database engines
            if getattr(self, 'ch_client', None) is not None:
                self.ch_client.close()

            _release_freed_memory()

            logger.log_info("Cleanup completed successfully")
            
        except Exception as e:
            logger.log_error(f"Error during cleanup: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()