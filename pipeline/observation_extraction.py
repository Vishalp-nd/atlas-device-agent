import os
import sys
import time
import threading
import json
import io
import tempfile
from datetime import datetime
import re
import traceback
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from urllib.parse import urlparse
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

import pandas as pd
import py7zr
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Text, Float, Integer, Boolean, TIMESTAMP, ARRAY, BigInteger
from sqlalchemy.pool import QueuePool
from sqlalchemy.dialects.postgresql import insert
from psycopg2.extras import execute_values, Json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), os.path.pardir)))

from db_login.db_login import db_connect_pool, read_db_config
from lib.logger import Logger
from lib.date_range import update_registry_data


dtype_dict = {
    "alerts_data": JSONB,
    "audio_events_data": JSONB,
    "events_data": JSONB,
    "speed_data": JSONB,
    "videometadata": JSONB,
    "canmetadata": JSONB,
    "user_generated_alert": JSONB,
    "idling_report": JSONB,
    "fuel_report": JSONB,
    "ir_led_hw_status": JSONB,
    "fields_transposed": JSONB,
    "one_fps": JSONB,
    "inertial_features": JSONB,
    "videometadataone": JSONB,
    "ignitions": JSONB,
    "session_embedding": JSONB,
    "burst_mode": JSONB,
    "inward_models_processed": JSONB,
    "outward_models_processed": JSONB,
    "dms_models_processed": JSONB
}

def insert_ignore_duplicates(table, conn, keys, data_iter):
    data = [dict(zip(keys, row)) for row in data_iter]
    stmt = insert(table.table).values(data)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["s3_path","start_time"]  # composite PK
    )
    conn.execute(stmt)

# Configuration constants
MAX_WORKERS_DEFAULT = int(os.getenv('DP_MAX_WORKERS', '24'))
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # in seconds
BATCH_SIZE = int(os.getenv('DP_BATCH_SIZE', '5000'))
CONNECTION_TIMEOUT = 60
POOL_SIZE = 10
MAX_OVERFLOW = 5
INSERT_WORKERS_DEFAULT = int(os.getenv('DP_INSERT_WORKERS', '4'))
S3_READ_CHUNK_SIZE = int(os.getenv('DP_S3_CHUNK_SIZE_MB', '8')) * 1024 * 1024

# Initialize logger
logger = Logger('data_processor')

EXTRACTEDDATA_SCHEMA_COLUMNS: Dict[str, str] = {
    'ota': 'TEXT',
    'udid': 'TEXT',
    'file_name': 'TEXT',
    'file_timestamp': 'DOUBLE PRECISION',
    'start_time': 'TIMESTAMP',
    'end_time': 'TIMESTAMP',
    'ignition_status': 'INTEGER',
    'uptime': 'INTEGER',
    'service_uptime': 'INTEGER',
    'privacymode': 'INTEGER',
    'dismode': 'TEXT',
    'voltage': 'DOUBLE PRECISION',
    'processing_mode': 'INTEGER',
    'inertial_processed': 'TEXT',
    'vision_processed': 'TEXT',
    'nrt_status': 'TEXT',
    'tripno': 'TEXT',
    'videometadatastatus': 'INTEGER',
    'min_speed': 'INTEGER',
    'max_speed': 'INTEGER',
    'sensormetadata_count': 'INTEGER',
    'driverinvariantsession': 'TEXT',
    'driverid': 'TEXT',
    'vehclass': 'TEXT',
    'vehicleid': 'TEXT',
    'cameras': 'INTEGER',
    'prevvideoname': 'TEXT',
    'current_videoname': 'TEXT',
    'nextvideoname': 'TEXT',
    'devicemodes_itemscount': 'INTEGER',
    'inference_data_itemscount': 'INTEGER',
    'alerts_data_num_alerts': 'INTEGER',
    'alerts_data': 'JSONB',
    'audio_events_num_alerts': 'INTEGER',
    'audio_events_data': 'JSONB',
    'events_data_num_alerts': 'INTEGER',
    'events_data': 'JSONB',
    'metadatastatus': 'TEXT',
    'device_id': 'TEXT',
    's3_path': 'TEXT',
    'speed_data': 'JSONB',
    'triggerid': 'INTEGER',
    'starttime': 'TEXT',
    'starttimeld': 'TEXT',
    'inwardstarttime': 'TEXT',
    'inwardstarttimeld': 'TEXT',
    'videometadata': 'JSONB',
    'rssi': 'INTEGER',
    'canmetadata': 'JSONB',
    'vin': 'TEXT',
    'can_firmware_ver': 'TEXT',
    'offset': 'INTEGER',
    'user_generated_alert': 'JSONB',
    'faceImageCaptured': 'BOOLEAN',
    'audioEnable': 'INTEGER',
    'obs_filetype': 'TEXT',
    'irled_status': 'INTEGER',
    'irled_states_timestamp': 'TEXT',
    'irled_states_status': 'TEXT',
    'prediction': 'TEXT',
    'obstructed_camera': 'INTEGER',
    'clear_low_confidence': 'INTEGER',
    'clear': 'INTEGER',
    'idling_report': 'JSONB',
    'fuel_report': 'JSONB',
    'video_ir_failure_cnf': 'INTEGER',
    'frame_ir_failure_cnfs': 'INTEGER[]',
    'ir_led_hw_status': 'JSONB',
    'frame_brightness_scores': 'INTEGER[]',
    'video_brightness_score': 'INTEGER',
    'num_ir_frames': 'INTEGER',
    'num_of_ir_enabled_frames': 'INTEGER',
    'ir_toggle_count': 'INTEGER',
    'num_dark_frames': 'INTEGER',
    'can_sn': 'TEXT',
    'json_size_kb': 'DOUBLE PRECISION',
    'observations_7z_size': 'DOUBLE PRECISION',
    'observations_device_7z_size': 'DOUBLE PRECISION',
    'fields_transposed': 'JSONB',
    'one_fps': 'JSONB',
    'worst_accel_str': 'TEXT',
    'worst_gyro_str': 'TEXT',
    'inertial_features': 'JSONB',
    'videometadataone': 'JSONB',
    'obs_data_optimization_version': 'TEXT',
    'ignitions': 'JSONB',
    'session_embedding': 'JSONB',
    'burst_mode': 'JSONB',
    'can_src': 'TEXT',
    'engine_status': 'TEXT',
    'num_frames_out': 'INTEGER',
    'num_frames_in': 'INTEGER',
    'num_frames_dms': 'INTEGER',
    'num_alerts': 'INTEGER',
    'inward_models_processed': 'JSONB',
    'outward_models_processed': 'JSONB',
    'dms_models_processed': 'JSONB',
    'is_inward_processed': 'BOOLEAN',
    'is_dms_processed': 'BOOLEAN',
    'protocol_info': 'TEXT',
    'tc_recommendation': 'TEXT',
    'session_type': 'TEXT',
}

@dataclass
class ProcessingMetrics:
    """Metrics for monitoring processing performance"""
    total_devices: int = 0
    successful_devices: int = 0
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
            'failed_devices': self.failed_devices,
            'total_files': self.total_files,
            'successful_files': self.successful_files,
            'failed_files': self.failed_files,
            'duration_seconds': (self.end_time - self.start_time).total_seconds() if self.start_time and self.end_time else None
        }

class DataProcessor:
    
    def __init__(self, s3_manager, trigger_hash:str, observation_only: bool = False):
        self.s3_manager = s3_manager
        self.trigger_hash = trigger_hash
        self.observation_only = observation_only or os.getenv('DP_OBSERVATION_ONLY', '0') == '1'
        self.env = None
        self.shared_s3_client = None
        self._thread_lock = threading.Lock()
        self.metrics = ProcessingMetrics()
        
        # Initialize database connections with proper error handling
        self._init_database_connections()
        
        # Performance monitoring
        self._last_progress_time = time.time()
        self._processed_count = 0

    def _init_database_connections(self) -> None:
        """Initialize database connections with proper error handling"""
        try:
            db_params = read_db_config('IRAVATH_TEST')
            
            # Create SQLAlchemy engine with optimized settings
            self.db_insert_engine = create_engine(
                f"postgresql://{db_params['user']}:{db_params['password']}@{db_params['host']}:{db_params['port']}/{db_params['database']}",
                poolclass=QueuePool,
                pool_size=POOL_SIZE,
                max_overflow=MAX_OVERFLOW,
                pool_pre_ping=True,
                pool_recycle=3600,  # Recycle connections after 1 hour
                connect_args={
                    "connect_timeout": CONNECTION_TIMEOUT,
                    "application_name": "data_processor"
                }
            )
            
            self.local_conn_pool = db_connect_pool('IRAVATH_TEST')
            self.obs_conn_pool = None

            # Ensure target table schema is ready before any insert path starts.
            self._ensure_extracteddata_schema()
            
            logger.log_info("Database connections initialized successfully")
            
        except Exception as e:
            logger.log_error(f"Failed to initialize database connections: {e}")
            raise

    def _ensure_extracteddata_schema(self) -> None:
        """Create/align extracteddata schema and indexes for resilient inserts."""
        try:
            base_table_sql = """
                CREATE TABLE IF NOT EXISTS public.extracteddata (
                    start_time TIMESTAMP NOT NULL,
                    s3_path TEXT NOT NULL,
                    CONSTRAINT extracteddata_pkey PRIMARY KEY (s3_path, start_time)
                )
            """

            with self.db_insert_engine.begin() as conn:
                conn.execute(text(base_table_sql))

                owner_info = conn.execute(
                    text(
                        """
                        SELECT pg_get_userbyid(c.relowner) AS owner_name,
                               current_user AS current_user_name
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                          AND c.relname = 'extracteddata'
                        LIMIT 1
                        """
                    )
                ).first()
                owner_name = owner_info[0] if owner_info else None
                current_user_name = owner_info[1] if owner_info else None
                can_manage_table_schema = owner_name == current_user_name

                existing_cols = conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'extracteddata'
                        """
                    )
                ).fetchall()
                existing_col_set = {row[0] for row in existing_cols}

                missing_columns = [
                    (col_name, col_type)
                    for col_name, col_type in EXTRACTEDDATA_SCHEMA_COLUMNS.items()
                    if col_name not in existing_col_set
                ]

                if missing_columns and can_manage_table_schema:
                    for col_name, col_type in missing_columns:
                        conn.execute(text(f'ALTER TABLE public.extracteddata ADD COLUMN "{col_name}" {col_type}'))
                elif missing_columns:
                    logger.log_warning(
                        f"Skipping {len(missing_columns)} missing-column additions on public.extracteddata because current user '{current_user_name}' does not own the table (owner: '{owner_name}')."
                    )

                conflict_key_exists = conn.execute(
                    text(
                        """
                        SELECT 1
                        FROM pg_constraint con
                        JOIN pg_class rel ON rel.oid = con.conrelid
                        JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                        WHERE nsp.nspname = 'public'
                          AND rel.relname = 'extracteddata'
                          AND con.contype IN ('p', 'u')
                          AND (
                                                                SELECT string_agg(att.attname::text, ',' ORDER BY arr.idx)
                                FROM unnest(con.conkey) WITH ORDINALITY AS arr(attnum, idx)
                                JOIN pg_attribute att
                                  ON att.attrelid = rel.oid
                                 AND att.attnum = arr.attnum
                                                    ) = 's3_path,start_time'
                        LIMIT 1
                        """
                    )
                ).first() is not None

                if not conflict_key_exists and can_manage_table_schema:
                    conn.execute(
                        text(
                            """
                            CREATE UNIQUE INDEX IF NOT EXISTS extracteddata_s3_path_start_time_uq
                            ON public.extracteddata (s3_path, start_time)
                            """
                        )
                    )
                elif not conflict_key_exists:
                    logger.log_warning(
                        f"Skipping conflict-key index creation on public.extracteddata because current user '{current_user_name}' does not own the table (owner: '{owner_name}')."
                    )

                if can_manage_table_schema:
                    conn.execute(
                        text(
                            """
                            CREATE INDEX IF NOT EXISTS idx_extracteddata_device_id
                            ON public.extracteddata USING btree (device_id)
                            """
                        )
                    )
                else:
                    logger.log_warning(
                        f"Skipping device_id index creation on public.extracteddata because current user '{current_user_name}' does not own the table (owner: '{owner_name}')."
                    )

            logger.log_info("Schema guard check complete for public.extracteddata")
        except Exception as e:
            logger.log_error(f"Schema guard failed for public.extracteddata: {e}")
            raise

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

                                try:
                                    with open(extracted_path, 'r', encoding='utf-8') as json_file:
                                        data = json.load(json_file)
                                    extracted_item = self._extract_data(data, url, self.trigger_hash)
                                    if extracted_item:
                                        extracted_data_local.append(extracted_item)
                                except json.JSONDecodeError as e:
                                    logger.log_error(f"Invalid JSON in file {name} from {url}: {e}")
                                except Exception as e:
                                    logger.log_error(f"Error processing file {name} from {url}: {e}")

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

    def _extract_data(self, data: Dict, url: str, trigger_hash: str) -> Optional[Dict]:
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
                return self._extract_observation_data(data, url, file_name, file_timestamp, device_id)

            # Keys to exclude from model processing lists
            EXCLUDED_INWARD_KEYS = {'annotation_image_scale','annotation_driver_side','process_fps','drop_packet','nrt_code_version','frame_rate','annotation_start_idx','frames_processed_info','process_rate','annotation_driving_side'}
            EXCLUDED_OUTWARD_KEYS = {'peak_intensity', 'locale', 'frameRate', 'highg_thread_retries', 'localSpeedUnit', 'videoDelay', 'splitVideoMode', 'dnnSubsampleFactor', 'lowg_thread_retries', 'module', 'highg_classification_enabled', 'lowg_classification_enabled', 'numFrames', 'laneCalSuccess', 'objTrackerMinTrackLength'}
            EXCLUDED_DMS_KEYS = {'nrt_code_version', 'privacy', 'frame_rate', 'process_fps', 'drop_packet', 'frames_processed_info', 'process_rate'}
            
            extracted_data = {
                'ota': data.get('app_ver'),
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
                'speed_data': {"speed": [item.get("speed") for item in video_metadata if isinstance(item, dict)]},
                'starttime': data.get('startTime'),
                'starttimeld': data.get('startTimeLd'),
                'inwardstarttime': data.get('inwardStartTime'),
                'inwardstarttimeld': data.get('inwardStartTimeLd'),
                'videometadata': video_metadata,
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
            }
            
            return extracted_data
            
        except Exception as e:
            logger.log_error(f"Error extracting data: {e}")
            return None

    def _extract_observation_data(
        self,
        data: Dict,
        url: str,
        file_name: Optional[str],
        file_timestamp: Optional[int],
        device_id: str,
    ) -> Dict:
        """Fast-path extraction for observation-oriented fields only."""
        inference_data = data.get('inference_data', {})
        observations = inference_data.get('observations_data', {}) if isinstance(inference_data, dict) else {}
        video_metadata = data.get('videoMetaData', [])

        return {
            'ota': data.get('app_ver'),
            'device_id': device_id,
            'udid': data.get('udid'),
            'file_name': file_name,
            'file_timestamp': file_timestamp,
            'start_time': self.epoch_to_utc(data.get('startTime')),
            'end_time': self.epoch_to_utc(data.get('endTime')),
            'starttime': data.get('startTime'),
            'starttimeld': data.get('startTimeLd'),
            's3_path': url,
            'videometadata': video_metadata if isinstance(video_metadata, list) else [],
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

    def _bulk_insert_rows(self, rows: List[Dict]) -> None:
        """Fast bulk insert with conflict handling using execute_values."""
        if not rows:
            return

        columns = list(rows[0].keys())
        values = []

        for row in rows:
            row_tuple = []
            for col in columns:
                val = row.get(col)
                if isinstance(val, (dict, list)):
                    row_tuple.append(Json(val))
                else:
                    row_tuple.append(val)
            values.append(tuple(row_tuple))

        col_sql = ', '.join('"' + col.replace('"', '""') + '"' for col in columns)
        sql = f"""
            INSERT INTO extracteddata ({col_sql})
            VALUES %s
            ON CONFLICT (s3_path, start_time) DO NOTHING
        """

        raw_conn = self.db_insert_engine.raw_connection()
        try:
            with raw_conn.cursor() as cur:
                execute_values(cur, sql, values, page_size=2000)
            raw_conn.commit()
        except Exception:
            raw_conn.rollback()
            raise
        finally:
            raw_conn.close()

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

    def insert_values(self, device_id: str, data: List[Dict], conn) -> None:
        """Insert data with improved error handling and validation"""
        if not data:
            logger.log_debug(f"No data to insert for device {device_id}")
            return
        
        try:
            data_df = pd.DataFrame(data)
            
            # Validate DataFrame
            if data_df.empty:
                logger.log_warning(f"Empty DataFrame for device {device_id}")
            else:
                # Insert with retry mechanism
                def insert_operation():
                    data_df.to_sql(
                        'extracteddata', 
                        conn, 
                        if_exists='append',
                        method=insert_ignore_duplicates, 
                        index=False, 
                        dtype=dtype_dict
                    )
                
                self._retry_operation(insert_operation)
                logger.log_info(f"Successfully inserted {len(data)} records for device {device_id}")
            
        except Exception as e:
            error_details = {
                'timestamp': datetime.now().isoformat(),
                'device_id': device_id,
                'error_type': type(e).__name__,
                'error_message': str(e),
                'traceback': traceback.format_exc(),
                'data_shape': data_df.shape if 'd' in locals() else 'N/A',
                'columns': list(data_df.columns) if 'data_df' in locals() else 'N/A',
                'connection_status': 'open' if conn and not conn.closed else 'closed',
            }
            
            logger.log_error(
                f"Database Insertion Error Report:\n"
                f"{'='*60}\n"
                f"Timestamp: {error_details['timestamp']}\n"
                f"Device ID: {error_details['device_id']}\n"
                f"Error Type: {error_details['error_type']}\n"
                f"Error Message: {error_details['error_message']}\n"
                f"Data Shape: {error_details['data_shape']}\n"
                f"Columns: {error_details['columns']}\n"
                f"Connection Status: {error_details['connection_status']}\n"
                f"{'='*60}\n"
                f"Full Traceback:\n{error_details['traceback']}"
            )
            raise

    def _log_progress(self, current: int, total: int, operation: str) -> None:
        """Log progress at intervals"""
        current_time = time.time()
        if current_time - self._last_progress_time >= 30:  # Log every 30 seconds
            percentage = (current / total * 100) if total > 0 else 0
            logger.log_info(f"{operation}: {current}/{total} ({percentage:.1f}%) completed")
            self._last_progress_time = current_time

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
                            self.metrics.failed_devices += 1
                            
                    except Exception as e:
                        self.metrics.failed_devices += 1
                        logger.log_error(f"S3 path fetch task failed: {e}")
                        
            logger.log_info(f"S3 path fetching completed. Successful: {self.metrics.successful_devices}, Failed: {self.metrics.failed_devices}")
            return s3_dict
            
        except Exception as e:
            logger.log_error(f"Critical error in process_data: {e}")
            raise
        finally:
            self.metrics.end_time = datetime.now()

    def insert_data_to_db(self, s3_dict: Dict) -> None:
        from collections import defaultdict
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        if not s3_dict:
            logger.log_info("No data to insert into the database")
            return

        start_time = datetime.now()
        logger.log_info(f"Starting database insertion for {len(s3_dict)} devices")

        # Hard limit DB writers
        INSERT_WORKERS = INSERT_WORKERS_DEFAULT
        insert_semaphore = threading.Semaphore(INSERT_WORKERS)
        
        # Constants for batch processing 
        batch_size = BATCH_SIZE

        # Buffer per device with thread-safe access
        device_data_buffer = defaultdict(list)
        device_counts = defaultdict(int)
        device_registry_data = {}
        buffer_lock = threading.Lock()

        max_workers = min(os.cpu_count() or 4, MAX_WORKERS_DEFAULT)

        logger.log_info(f"Processing URLs using {max_workers} workers with batch size {batch_size}")

        def insert_device_batch(device_id: str, rows: list):
            """Insert a batch of data for a device"""
            if not rows:
                return

            logger.log_info(f"Inserting batch for device {device_id}, rows={len(rows)}")

            with insert_semaphore:
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
                        'batch_rows': len(rows),
                        'columns': list(rows[0].keys()) if rows else []
                    }
                    
                    logger.log_error(
                        f"Database Insertion Error Report:\n"
                        f"{'='*60}\n"
                        f"Timestamp: {error_details['timestamp']}\n"
                        f"Device ID: {error_details['device_id']}\n"
                        f"Error Type: {error_details['error_type']}\n"
                        f"Error Message: {error_details['error_message']}\n"
                        f"Batch Rows: {error_details['batch_rows']}\n"
                        f"Columns: {error_details['columns']}\n"
                        f"{'='*60}\n"
                        f"Full Traceback:\n{error_details['traceback']}"
                    )
                    raise

        try:
            # -----------------------------
            # STEP 1: PROCESS URLS (PARALLEL)
            # -----------------------------
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="URLProcess") as executor:
                futures = []

                for device_id, data in s3_dict.items():
                    urls, date_range = data
                    device_registry_data[device_id] = date_range

                    for url in urls:
                        futures.append(
                            executor.submit(self.process_url, device_id, url, date_range)
                        )

                # Process completed tasks with batch insertion
                with ThreadPoolExecutor(max_workers=INSERT_WORKERS, thread_name_prefix="BatchInsert") as batch_executor:
                    for future in as_completed(futures):
                        try:
                            device_id, extracted_data, _ = future.result()
                            if extracted_data:
                                with buffer_lock:
                                    device_data_buffer[device_id].extend(extracted_data)
                                    device_counts[device_id] += len(extracted_data)
                                    
                                    # Check if we need to insert a batch
                                    if device_counts[device_id] >= batch_size:
                                        # Extract batch data
                                        batch_data = device_data_buffer[device_id][:batch_size]
                                        device_data_buffer[device_id] = device_data_buffer[device_id][batch_size:]
                                        device_counts[device_id] -= batch_size
                                        
                                        # Submit batch for insertion
                                        batch_executor.submit(insert_device_batch, device_id, batch_data)
                                        
                        except Exception as e:
                            logger.log_error(f"URL processing failed: {e}")

            logger.log_info("URL processing completed")
            logger.log_info(
                f"Devices with remaining data: {len(device_data_buffer)} / {len(s3_dict)}"
            )

            # -----------------------------
            # STEP 2: INSERT REMAINING DATA
            # -----------------------------
            logger.log_info("Inserting remaining data for all devices")
            
            with ThreadPoolExecutor(max_workers=INSERT_WORKERS, thread_name_prefix="FinalInsert") as final_executor:
                final_futures = []
                
                for device_id, remaining_data in device_data_buffer.items():
                    if remaining_data:  # Insert any remaining data that didn't fill a complete batch
                        logger.log_info(f"Inserting remaining {len(remaining_data)} records for device {device_id}")
                        final_futures.append(
                            final_executor.submit(insert_device_batch, device_id, remaining_data)
                        )
                
                # Wait for all final insertions to complete
                for future in as_completed(final_futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.log_error(f"Final insertion failed: {e}")

            # -----------------------------
            # STEP 3: UPDATE REGISTRY
            # -----------------------------
            self._update_registry_data(device_registry_data)

            duration = (datetime.now() - start_time).total_seconds()
            logger.log_info(f"Database insertion completed in {duration:.2f}s using batch processing")

        except Exception as e:
            logger.log_error(f"Critical error in insert_data_to_db: {e}")
            raise

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
            if hasattr(self, 'db_insert_engine'):
                self.db_insert_engine.dispose()
            
            logger.log_info("Cleanup completed successfully")
            
        except Exception as e:
            logger.log_error(f"Error during cleanup: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()