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


def _product_family_slug(product_lines):
    if not product_lines:
        return "all"
    if len(product_lines) == 1:
        return product_lines[0]
    return "__".join(product_lines)


def _output_root_for_product_lines(product_lines):
    return os.path.join(current_dir, "OUTPUT", _product_family_slug(product_lines))


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
    allowed_ota_versions_raw = os.getenv("OBS_OTA_VERSIONS")
    ota_env_source = "OBS_OTA_VERSIONS"
    if allowed_ota_versions_raw is None:
        allowed_ota_versions_raw = os.getenv("OBS_OTA_VERSION", "")
        ota_env_source = "OBS_OTA_VERSION"
    allowed_ota_versions = _parse_csv_env(allowed_ota_versions_raw or "")

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
        f"ALLOWED_OTA_VERSIONS={allowed_ota_versions if allowed_ota_versions else 'ALL'}, "
        f"OTA_ENV_SOURCE={ota_env_source}"
    )

    if not allowed_ota_versions:
        logging.log_info(
            "No OTA filter configured via OBS_OTA_VERSIONS/OBS_OTA_VERSION; "
            "all discovered OTA versions will be processed for selected product lines."
        )

    return product_lines, allowed_ota_versions


def _resolve_versions_to_process(discovered_versions_by_family, product_lines, allowed_ota_versions):
    family_config = getattr(regionUS, "FAMILY_CONFIG", {})
    selected_families = product_lines if product_lines else list(family_config.keys())

    # Priority 1: constrain the candidate set by product line first.
    selected_discovered_versions = []
    for family in selected_families:
        versions = discovered_versions_by_family.get(family)
        if isinstance(versions, list):
            selected_discovered_versions.extend(versions)
        elif versions:
            selected_discovered_versions.append(versions)

    if selected_discovered_versions:
        # Keep deterministic ordering while removing duplicates.
        selected_discovered_versions = list(dict.fromkeys(selected_discovered_versions))

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


def get_all_device_versions_by_family():
    try:
        logging.log_info("Starting get_all_device_versions_by_family function")
        us = regionUS()
        versions_by_family = us.all_versions_by_family()

        for family, versions in versions_by_family.items():
            logging.log_info(
                f"Discovered {len(versions)} OTA version(s) for product line '{family}'"
            )

        total_versions = sum(len(v) for v in versions_by_family.values())
        logging.log_info(
            "Total device versions retrieved across product lines: "
            f"{total_versions}"
        )
        return versions_by_family
    except Exception as e:
        logging.log_error(f"Error in get_all_device_versions_by_family: {e}")
        raise


def daily_device_extraction(ys=False):
    try:
        logging.log_info("Starting daily device extraction process")
        device_setup_script = os.path.join(REPO_ROOT, "device_data_setup/main_device_setup.py")
        product_lines, allowed_ota_versions = _load_runtime_filters()
        output_root = _output_root_for_product_lines(product_lines)
        trigger_folder_path = os.path.join(output_root, "polling", datetime.date.today().strftime('%Y-%m-%d'))
        logging.log_info(f"Using product-family output root: {output_root}")
        logging.log_info(f"Creating trigger folder at: {trigger_folder_path}")
        os.makedirs(trigger_folder_path, exist_ok=True)
        logging.log_info(f"Trigger folder created successfully at: {trigger_folder_path}")

        if allowed_ota_versions:
            logging.log_info(
                "Using explicit OTA filter values; discovery will use latest-per-family map "
                "and then apply the provided OTA versions."
            )
            discovered_versions = get_latest_device()
        else:
            logging.log_info(
                "Using all-versions discovery because OTA filter is empty."
            )
            discovered_versions = get_all_device_versions_by_family()
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
                        output_dir=output_root,
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
                    device_data_csv_path = os.path.join(output_root, f"trigger_{trigger_id}", "device_list.csv")
                else:
                    device_data_csv_path = os.path.join(output_root, f"trigger_{trigger_id}", "device_list_config_mapped.csv")
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

                feature_level_csv = os.path.join(output_root, f"trigger_{trigger_id}", "Feature_level_summary.csv")
                feature_level_final_csv_path = os.path.join(trigger_folder_path, f"feature_level_summary_{version}.csv")
                if os.path.exists(feature_level_csv):
                    logging.log_info(f"Copying {feature_level_csv} to {feature_level_final_csv_path}")
                    shutil.copy(feature_level_csv, feature_level_final_csv_path)
                    logging.log_info(f"Successfully copied feature level summary for version {version}")
                else:
                    logging.log_warning(f"Source file not found: {feature_level_csv}")

                config_csv = os.path.join(output_root, f"trigger_{trigger_id}", "device_list_config.csv")
                config_final_csv_path = os.path.join(trigger_folder_path, f"device_config_{version}.csv")
                if os.path.exists(config_csv):
                    logging.log_info(f"Copying {config_csv} to {config_final_csv_path}")
                    shutil.copy(config_csv, config_final_csv_path)
                    logging.log_info(f"Successfully copied device config for version {version}")
                else:
                    logging.log_warning(f"Source file not found: {config_csv}")

                feature_xlsx = os.path.join(output_root, f"trigger_{trigger_id}", "Feature_device_summary.xlsx")
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


def _populate_obs_for_window(target_date, start_datetime, end_datetime):
    product_lines, _ = _load_runtime_filters()
    trigger_folder = os.path.join(
        _output_root_for_product_lines(product_lines),
        "polling",
        target_date.strftime('%Y-%m-%d')
    )

    if not os.path.exists(trigger_folder):
        logging.log_error(f"Trigger folder does not exist: {trigger_folder}")

        if target_date == datetime.date.today():
            logging.log_info("Attempting device extraction for today to create missing trigger folder")
            daily_device_extraction(ys=False)
        elif target_date == datetime.date.today() - datetime.timedelta(days=1):
            logging.log_info("Attempting device extraction for yesterday to create missing trigger folder")
            daily_device_extraction(ys=True)
        else:
            logging.log_warning(
                "Automatic extraction is only supported for today/yesterday when trigger folder is missing. "
                f"Skipping date {target_date.strftime('%Y-%m-%d')}"
            )
            return

        if not os.path.exists(trigger_folder):
            logging.log_warning(f"Trigger folder still missing after extraction attempt: {trigger_folder}")
            return

    start_date_str = start_datetime.strftime('%Y-%m-%d %H:%M:%S')
    end_date_str = end_datetime.strftime('%Y-%m-%d %H:%M:%S')

    logging.log_debug(f"Processing date range: {start_date_str} to {end_date_str}")

    csv_files = []
    for file in os.listdir(trigger_folder):
        if file.startswith('device_data_') and file.endswith('.csv'):
            csv_files.append(os.path.join(trigger_folder, file))

    logging.log_info(
        f"Found {len(csv_files)} device data CSV files to process for date {target_date.strftime('%Y-%m-%d')}"
    )

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
                obs_processor(csv_file, int(target_date.strftime('%Y%m%d')))
                process_healthstats(csv_file)
                logging.log_info(f"Successfully processed file: {csv_file}")
            except Exception as e:
                logging.log_error(f"Error processing file {csv_file} with obs_processor: {e}")
                raise e


def extracteddata_obs_population(sd_str, ed_str, ys=False):
    try:
        logging.log_info(f"Starting extracted data obs population for time range: {sd_str} - {ed_str}")
        if ys is True:
            today = datetime.date.today() - datetime.timedelta(days=1)
        else:
            today = datetime.date.today()

        start_time = datetime.datetime.strptime(sd_str, '%H:%M:%S').time()
        end_time = datetime.datetime.strptime(ed_str, '%H:%M:%S').time()

        start_datetime = datetime.datetime.combine(today, start_time)
        end_datetime = datetime.datetime.combine(today, end_time)

        _populate_obs_for_window(today, start_datetime, end_datetime)

        logging.log_info(f"Extracted data obs population completed for time range: {sd_str} - {ed_str}")

    except Exception as e:
        logging.log_critical(f"Critical error in extracteddata_obs_population: {e}")
        raise


def extracteddata_obs_population_datetime_range(start_dt_str, end_dt_str):
    try:
        start_datetime = datetime.datetime.strptime(start_dt_str, '%Y-%m-%d %H:%M:%S')
        end_datetime = datetime.datetime.strptime(end_dt_str, '%Y-%m-%d %H:%M:%S')

        if end_datetime < start_datetime:
            raise ValueError("--end-dt must be greater than or equal to --start-dt")

        logging.log_info(
            "Starting extracted data obs population for datetime range: "
            f"{start_datetime.strftime('%Y-%m-%d %H:%M:%S')} - {end_datetime.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        current_date = start_datetime.date()
        while current_date <= end_datetime.date():
            day_start = datetime.datetime.combine(current_date, datetime.time.min)
            day_end = datetime.datetime.combine(current_date, datetime.time(23, 59, 59))

            window_start = max(start_datetime, day_start)
            window_end = min(end_datetime, day_end)

            logging.log_info(
                "Processing day window: "
                f"{window_start.strftime('%Y-%m-%d %H:%M:%S')} - {window_end.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            _populate_obs_for_window(current_date, window_start, window_end)
            current_date += datetime.timedelta(days=1)

        logging.log_info("Extracted data obs population completed for datetime range")

    except Exception as e:
        logging.log_critical(f"Critical error in extracteddata_obs_population_datetime_range: {e}")
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
        help="Run extracted-data obs population for a time window or full datetime range",
    )
    obs_parser.add_argument("--sd", help="Start time HH:MM:SS (legacy mode)")
    obs_parser.add_argument("--ed", help="End time HH:MM:SS (legacy mode)")
    obs_parser.add_argument(
        "--ys",
        action="store_true",
        default=False,
        help="Use yesterday's date instead of today (legacy mode)",
    )
    obs_parser.add_argument(
        "--start-dt",
        help="Start datetime in 'YYYY-MM-DD HH:MM:SS'",
    )
    obs_parser.add_argument(
        "--end-dt",
        help="End datetime in 'YYYY-MM-DD HH:MM:SS'",
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
            has_datetime_range = bool(args.start_dt or args.end_dt)
            if has_datetime_range:
                if not (args.start_dt and args.end_dt):
                    raise ValueError("Both --start-dt and --end-dt are required when using datetime mode")
                if args.sd or args.ed or args.ys:
                    logging.log_warning(
                        "Ignoring --sd/--ed/--ys because --start-dt/--end-dt datetime mode was provided"
                    )
                extracteddata_obs_population_datetime_range(args.start_dt, args.end_dt)
            else:
                if not (args.sd and args.ed):
                    raise ValueError(
                        "Provide either --start-dt/--end-dt or legacy --sd/--ed (with optional --ys)"
                    )
                extracteddata_obs_population(args.sd, args.ed, ys=args.ys)

        elif args.command == "extract":
            daily_device_extraction(ys=args.ys)

        logging.log_info(f"Command '{args.command}' completed successfully")

    except Exception as e:
        logging.log_critical(f"Command '{args.command}' failed: {e}")
        sys.exit(1)
