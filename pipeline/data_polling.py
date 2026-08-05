import argparse
import atexit
import fcntl
import shutil
import os
import subprocess
import sys
import pandas as pd
import datetime
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, os.path.pardir))

sys.path.append(REPO_ROOT)

from lib.logger import Logger
from lib.device_list_fetcher import fetch_device_list
from lib.fetch_data import regionUS
from lib.extracteddata_population import obs_processor
try:
    from lib.healthstats_processor import process_healthstats
except ImportError:
    def process_healthstats(csv_file):
        return None


logging = Logger('data_polling')

current_dir = REPO_ROOT
logging.log_info(f"Current directory: {current_dir}")

LOCK_FILE = os.path.join(current_dir, "OUTPUT/.data_polling.lock")
_lock_fd = None

DEFAULT_ENV_PATH = os.path.join(REPO_ROOT, ".env")


def acquire_lock():
    """Acquire an exclusive file lock, waiting if another instance is running."""
    global _lock_fd
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    _lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logging.log_info("Another data_polling instance is running. Queuing up and waiting...")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX)
    _lock_fd.write(str(os.getpid()))
    _lock_fd.flush()
    logging.log_info(f"Lock acquired (pid {os.getpid()})")


def release_lock():
    """Release the file lock."""
    global _lock_fd
    if _lock_fd:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
            logging.log_info("Lock released")
        except Exception as e:
            logging.log_warning(f"Error releasing lock: {e}")


def _parse_csv_env(value: str):
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_runtime_filters():
    load_dotenv(DEFAULT_ENV_PATH, override=False)

    product_lines = [item.lower() for item in _parse_csv_env(os.getenv("PRODUCT_LINES", ""))]
    allowed_ota_versions = _parse_csv_env(os.getenv("ALLOWED_OTA_VERSIONS", ""))

    family_config = getattr(regionUS, "FAMILY_CONFIG", {})
    valid_product_lines = set(family_config.keys())
    invalid_product_lines = [name for name in product_lines if name not in valid_product_lines]
    if invalid_product_lines:
        valid_values = ", ".join(sorted(valid_product_lines))
        raise ValueError(
            "Invalid PRODUCT_LINES value(s): "
            f"{', '.join(invalid_product_lines)}. "
            f"Allowed values: {valid_values}."
        )

    logging.log_info(
        "Runtime filters loaded - "
        f"PRODUCT_LINES={product_lines if product_lines else 'ALL'}, "
        f"ALLOWED_OTA_VERSIONS={allowed_ota_versions if allowed_ota_versions else 'ALL'}"
    )

    return product_lines, allowed_ota_versions


def _resolve_versions_to_process(discovered_versions_by_family, product_lines, allowed_ota_versions):
    family_config = getattr(regionUS, "FAMILY_CONFIG", {})
    selected_families = product_lines if product_lines else list(family_config.keys())

    # Priority 1: constrain the candidate set by product line first.
    selected_discovered_versions = []
    for family in selected_families:
        version = discovered_versions_by_family.get(family)
        if version:
            selected_discovered_versions.append(version)

    if not allowed_ota_versions:
        return selected_discovered_versions

    # Priority 2: apply exact OTA filtering within the selected product-line scope.
    if not product_lines:
        return allowed_ota_versions

    allowed_prefixes = {
        family_config[family][1]
        for family in selected_families
        if family in family_config
    }
    filtered_ota_versions = [
        ota for ota in allowed_ota_versions
        if any(ota.startswith(prefix) for prefix in allowed_prefixes)
    ]
    return filtered_ota_versions


def get_latest_device():
    try:
        logging.log_info("Starting get_latest_device function")
        us = regionUS()
        logging.log_info("regionUS instance created successfully")

        versions = {}
        for region in [us]:
            for attr_name, version in region.__dict__.items():
                if attr_name == "s3":
                    continue
                if version:
                    versions[attr_name] = version
                    logging.log_info(f"Found device version: {attr_name} = {version}")
                else:
                    logging.log_warning(f"No version found for device: {attr_name}")

        logging.log_info(f"Total device versions retrieved: {len(versions)}")
        return versions
    except Exception as e:
        logging.log_error(f"Error in get_latest_device: {e}")
        raise


def daily_device_extraction(ys=False):
    try:
        logging.log_info("Starting daily device extraction process")
        device_setup_script = os.path.join(REPO_ROOT, "device_data_setup/main_device_setup.py")
        trigger_folder_path = os.path.join(current_dir, f"OUTPUT/polling/{datetime.date.today().strftime('%Y-%m-%d')}")
        logging.log_info(f"Creating trigger folder at: {trigger_folder_path}")
        os.makedirs(trigger_folder_path, exist_ok=True)
        logging.log_info(f"Trigger folder created successfully at: {trigger_folder_path}")

        product_lines, allowed_ota_versions = _load_runtime_filters()
        discovered_versions = get_latest_device()
        versions = _resolve_versions_to_process(discovered_versions, product_lines, allowed_ota_versions)
        today = datetime.date.today()
        logging.log_info(f"Discovered versions by product line: {discovered_versions}")
        logging.log_info(f"Device versions to process after filters: {versions}")

        if not versions:
            logging.log_warning(
                "No device versions found after applying PRODUCT_LINES and ALLOWED_OTA_VERSIONS filters, skipping extraction"
            )
            return

        use_fallback_fetcher = not os.path.exists(device_setup_script)
        if use_fallback_fetcher:
            logging.log_warning(
                f"Missing {device_setup_script}. Falling back to direct device list fetch from PROD_DB."
            )

        for version in versions:
            try:
                logging.log_info(f"Processing device version: {version}")
                start_date = datetime.datetime.combine(today, datetime.time.min)
                if ys:
                    start_date -= datetime.timedelta(days=1)
                end_date = start_date + datetime.timedelta(hours=3)
                if ys:
                    end_date -= datetime.timedelta(days=1)
                start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
                end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S')
                env = "production"
                tags_str = "[]"

                logging.log_debug(f"Processing parameters - Version: {version}, Start: {start_date_str}, End: {end_date_str}, Env: {env}")

                if use_fallback_fetcher:
                    logging.log_info(f"Fetching device list directly for version {version}")
                    device_list_csv = fetch_device_list(
                        trigger_hash=version,
                        output_dir=os.path.join(current_dir, "OUTPUT"),
                        ota_versions=[version],
                        section='PROD_DB',
                    )
                    logging.log_info(f"Direct device list fetch completed: {device_list_csv}")
                else:
                    # Run device data setup
                    logging.log_info(f"Running device data setup for version {version}")
                    subprocess.run(['python3', device_setup_script, '-t', version, '-o', version, '-sd', start_date_str, '-ed', end_date_str, '-env', env, '-tag', tags_str], check=True, cwd=REPO_ROOT)
                    logging.log_info(f"Device data setup completed successfully for version {version}")

            except subprocess.CalledProcessError as e:
                logging.log_error(f"Device data setup failed for version {version}: {e}")
                continue
            except Exception as e:
                logging.log_error(f"Device list preparation failed for version {version}: {e}")
                continue

            try:
                trigger_id = version  # Version is being used as trigger_id for data_polling
                logging.log_info(f"Starting file copy operations for version {version}")

                if use_fallback_fetcher:
                    device_data_csv_path = os.path.join(current_dir, f"OUTPUT/trigger_{trigger_id}/device_list.csv")
                else:
                    device_data_csv_path = os.path.join(current_dir, f"OUTPUT/trigger_{trigger_id}/device_list_config_mapped.csv")
                device_data_final_csv_path = os.path.join(trigger_folder_path, f"device_data_{version}.csv")
                if os.path.exists(device_data_csv_path):
                    logging.log_info(f"Copying {device_data_csv_path} to {device_data_final_csv_path}")
                    if use_fallback_fetcher:
                        df = pd.read_csv(device_data_csv_path)
                        df['environment'] = env
                        df.to_csv(device_data_final_csv_path, index=False)
                    else:
                        shutil.copy(device_data_csv_path, device_data_final_csv_path)
                    logging.log_info(f"Successfully copied device data CSV for version {version}")
                else:
                    logging.log_warning(f"Source file not found: {device_data_csv_path}")

                feature_level_csv = os.path.join(current_dir, f"OUTPUT/trigger_{trigger_id}/Feature_level_summary.csv")
                feature_level_final_csv_path = os.path.join(trigger_folder_path, f"feature_level_summary_{version}.csv")
                if os.path.exists(feature_level_csv):
                    logging.log_info(f"Copying {feature_level_csv} to {feature_level_final_csv_path}")
                    shutil.copy(feature_level_csv, feature_level_final_csv_path)
                    logging.log_info(f"Successfully copied feature level summary for version {version}")
                else:
                    logging.log_warning(f"Source file not found: {feature_level_csv}")

                config_csv = os.path.join(current_dir, f"OUTPUT/trigger_{trigger_id}/device_list_config.csv")
                config_final_csv_path = os.path.join(trigger_folder_path, f"device_config_{version}.csv")
                if os.path.exists(config_csv):
                    logging.log_info(f"Copying {config_csv} to {config_final_csv_path}")
                    shutil.copy(config_csv, config_final_csv_path)
                    logging.log_info(f"Successfully copied device config for version {version}")
                else:
                    logging.log_warning(f"Source file not found: {config_csv}")

                feature_xlsx = os.path.join(current_dir, f"OUTPUT/trigger_{trigger_id}/Feature_device_summary.xlsx")
                feature_xlsx_final_path = os.path.join(trigger_folder_path, f"feature_device_{version}.xlsx")
                if os.path.exists(feature_xlsx):
                    logging.log_info(f"Copying {feature_xlsx} to {feature_xlsx_final_path}")
                    shutil.copy(feature_xlsx, feature_xlsx_final_path)
                    logging.log_info(f"Successfully copied feature device summary for version {version}")
                else:
                    logging.log_warning(f"Source file not found: {feature_xlsx}")

                logging.log_info(f"All file copy operations completed for version {version}")

            except Exception as e:
                logging.log_error(f"File copy operation failed for version {version}: {e}")
                raise

        logging.log_info("Starting extracted data obs population")

    except Exception as e:
        logging.log_critical(f"Critical error in daily_device_extraction: {e}")
        raise


def extracteddata_obs_population(sd_str, ed_str, ys=False):
    try:
        logging.log_info(f"Starting extracted data obs population for time range: {sd_str} - {ed_str}")
        if ys is True:
            today = datetime.date.today() - datetime.timedelta(days=1)
        else:
            today = datetime.date.today()
        trigger_folder = os.path.join(current_dir, f"OUTPUT/polling/{today.strftime('%Y-%m-%d')}")

        if not os.path.exists(trigger_folder):
            logging.log_error(f"Trigger folder does not exist: {trigger_folder}")
            daily_device_extraction(ys)
            if not os.path.exists(trigger_folder):
                logging.log_warning(f"Trigger folder still missing after extraction attempt: {trigger_folder}")
                return

        start_time = datetime.datetime.strptime(sd_str, '%H:%M:%S').time()
        end_time = datetime.datetime.strptime(ed_str, '%H:%M:%S').time()

        start_datetime = datetime.datetime.combine(today, start_time)
        end_datetime = datetime.datetime.combine(today, end_time)

        start_date_str = start_datetime.strftime('%Y-%m-%d %H:%M:%S')
        end_date_str = end_datetime.strftime('%Y-%m-%d %H:%M:%S')

        logging.log_debug(f"Processing date range: {start_date_str} to {end_date_str}")

        csv_files = []
        for file in os.listdir(trigger_folder):
            if file.startswith('device_data_') and file.endswith('.csv'):
                csv_files.append(os.path.join(trigger_folder, file))

        logging.log_info(f"Found {len(csv_files)} device data CSV files to process")

        if not csv_files:
            logging.log_warning("No device data CSV files found in trigger folder")
            return

        for csv_file in csv_files:
            try:
                if os.path.exists(csv_file):
                    logging.log_debug(f"Updating CSV file with date information: {csv_file}")
                    df = pd.read_csv(csv_file)
                    df['start_date'] = start_date_str
                    df['end_date'] = end_date_str
                    df.to_csv(csv_file, index=False)
                    logging.log_info(f"Successfully updated CSV file: {csv_file}")
                else:
                    logging.log_warning(f"CSV file does not exist: {csv_file}")
                    continue
            except Exception as e:
                logging.log_error(f"Error updating CSV file {csv_file}: {e}")
                continue

        for csv_file in csv_files:
            if os.path.exists(csv_file):
                try:
                    logging.log_info(f"Processing file with obs_processor: {csv_file}")
                    obs_processor(csv_file, int(today.strftime('%Y%m%d')))
                    process_healthstats(csv_file)
                    logging.log_info(f"Successfully processed file: {csv_file}")
                except Exception as e:
                    logging.log_error(f"Error processing file {csv_file} with obs_processor: {e}")
                    raise e

        logging.log_info(f"Extracted data obs population completed for time range: {sd_str} - {ed_str}")

    except Exception as e:
        logging.log_critical(f"Critical error in extracteddata_obs_population: {e}")
        raise


def midnight_tasks():
    try:
        logging.log_info("Starting midnight tasks")
        extracteddata_obs_population("15:00:00", "17:59:59", ys=True)
        daily_device_extraction()
        logging.log_info("Midnight tasks completed successfully")
    except Exception as e:
        logging.log_critical(f"Critical error in midnight_tasks: {e}")
        raise


def parse_args():
    parser = argparse.ArgumentParser(
        description="Data Polling - Run individual tasks via cron instead of a persistent scheduler."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "midnight",
        help="Run midnight tasks (yesterday obs 15:00-17:59 + daily device extraction)",
    )

    obs_parser = subparsers.add_parser(
        "obs",
        help="Run extracted-data obs population for a given time window",
    )
    obs_parser.add_argument("--sd", required=True, help="Start time HH:MM:SS")
    obs_parser.add_argument("--ed", required=True, help="End time HH:MM:SS")
    obs_parser.add_argument(
        "--ys",
        action="store_true",
        default=False,
        help="Use yesterday's date instead of today",
    )

    extract_parser = subparsers.add_parser(
        "extract",
        help="Run daily device extraction only",
    )
    extract_parser.add_argument(
        "--ys",
        action="store_true",
        default=False,
        help="Use yesterday's date instead of today",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    acquire_lock()
    atexit.register(release_lock)

    logging.log_info(f"data_polling invoked with command: {args.command}")
    try:
        if args.command == "midnight":
            midnight_tasks()

        elif args.command == "obs":
            extracteddata_obs_population(args.sd, args.ed, ys=args.ys)

        elif args.command == "extract":
            daily_device_extraction(ys=args.ys)

        logging.log_info(f"Command '{args.command}' completed successfully")

    except Exception as e:
        logging.log_critical(f"Command '{args.command}' failed: {e}")
        sys.exit(1)
