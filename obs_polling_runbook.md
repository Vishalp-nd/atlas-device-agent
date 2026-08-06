# Obs Data Polling — Runbook & Environment Checklist

---

## 1. Repository & File Locations

| Item | Path |
|---|---|
| Main runner | `data_polling.py` (project root) |
| S3 path fetching | `lib/s3_manager.py` |
| Obs processor entry | `lib/extracteddata_population.py` |
| Data extraction & DB insert | `lib/data_processor_v2.py` |
| DB credentials | `db_login/db_credentials.ini` |
| DB connection helpers | `db_login/db_login.py` |
| Date range / registry logic | `lib/date_range.py` |
| Device version discovery | `lib/fetch_data.py` |
| Cron shell wrapper | `cron/data_polling.sh` |

**Repository:** `git@github.com:Sainathg-nd/Airavath.git`  
**Branch with complete runnable flow:** `iravath_2.0`

---

## 2. AWS & EC2

### Region
| Service | Region |
|---|---|
| S3, DynamoDB, EC2 | `us-west-2` |
| SSM | `us-west-1` |

### S3 Buckets & Prefixes

| Purpose | Bucket | Prefix |
|---|---|---|
| Stage 1 — OTA version discovery | `idms-production` | `ota_packages/<hw>/` e.g. `ota_packages/krait/` |
| Stage 4 — Obs `.7z` session archives | Parsed at runtime from `s3_zip_file_path` in `nddeduplication` | No fixed prefix — URL is stored verbatim in the source DB |
| Health stats (parallel flow) | `idms-production` (prod) / `idms-staging` (staging) | Device-specific path |
| CAN data (parallel flow) | `fleetdata-production` / `fleetdata-staging` | Device-specific path |

> The obs `.7z` bucket name is not hardcoded. `data_processor_v2.py` parses it dynamically:  
> `bucket_name = parsed_url.netloc.split('.')[0]`  
> Whatever hostname prefix is in the `s3_zip_file_path` URLs from `nddeduplication` becomes the bucket name.

### AWS Credential Profile
Resolved in `lib/s3_manager.py`:
```python
if os.getlogin() == 'deviceqa':
    self.profile_name = 'iravath'   # named profile on the DevQA workstation
else:
    self.profile_name = 'default'   # falls back to instance role on EC2
```
On an EC2 instance the `default` profile resolves via the instance metadata service (IMDS), so **an IAM instance profile role must be attached**.

### Required IAM Permissions

| Permission | Resource | Used by |
|---|---|---|
| `s3:ListBucket` | `arn:aws:s3:::idms-production` | `regionUS.get_latest_version()` — lists OTA package keys |
| `s3:GetObject` | `arn:aws:s3:::idms-production/*` | OTA stage |
| `s3:GetObject` | `arn:aws:s3:::<obs-zip-bucket>/*` | `DataProcessor.process_url()` — downloads `.7z` files |
| `ssm:GetParameter` / `ssm:DescribeParameters` | (optional) | Only if SSM-based secret fetching is added — not currently used in this flow |
| `sts:GetCallerIdentity` | `*` | Implicit SDK credential validation |

> **VPC endpoint:** The code makes no VPC endpoint configuration — it connects to S3 over the public endpoint. If the EC2 instance is in a private subnet with no NAT, an **S3 VPC Gateway Endpoint** must be configured in the VPC routing table for the flow to reach S3 without internet access.

---

## 3. Database Connection Details

All credentials are read from `db_login/db_credentials.ini` via `db_login.db_login.read_db_config(section)`.

### POLL_USER_DB — local destination DB (extracteddata, registry)
```ini
[POLL_USER_DB]
database = iravath_stag
host     = localhost
user     = bot_user
password = admin
port     = 5432
```

### PROD_OBS_DB — production observation source (nddeduplication)
```ini
[PROD_OBS_DB]
database = beta-prod-idms-db
host     = pg-observations-production-ro.netradyne.info
user     = sainath.gandla
password = <redacted>
port     = 5432
```

### STAG_OBS_DB — staging observation source (nddeduplication)
```ini
[STAG_OBS_DB]
database = netradyne-testing
host     = pg-observations-staging.netradyne.info
user     = sainath.gandla
password = <redacted>
port     = 5432
```

### Required DB Privileges

| DB | Table | Privileges needed |
|---|---|---|
| PROD_OBS_DB / STAG_OBS_DB | `nddeduplication` | `SELECT` |
| POLL_USER_DB | `extracteddata` | `SELECT`, `INSERT`, `CREATE INDEX` (schema guard adds indexes) |
| POLL_USER_DB | `extracteddata_registry` | `SELECT`, `INSERT`, `UPDATE`, `ALTER` (schema guard adds `ota` column if missing) |

The `bot_user` local user has `GRANT ALL ON SCHEMA public` per `psql/data_db_scripts.sql`.

---

## 4. Runtime Setup

### Python Environment
```bash
source /data/iravath_ws/pyEnv/iravath/bin/activate
```
All required packages are in `requirements.txt`, including:
```
boto3==1.36.12
pandas==2.2.3
psycopg2-binary==2.9.10
py7zr==0.22.0
SQLAlchemy==2.0.37
```

### Working Directory
Must be the **project root** (`/home/vishalpraveen/Documents/Airavath` or equivalent). All `subprocess.run(['python3', 'device_data_setup/...'])` calls and relative `OUTPUT/` paths resolve from there.
```bash
cd /path/to/Airavath
python3 data_polling.py obs --sd 09:00:00 --ed 11:59:59
```

### Log Output
Auto-created at `OUTPUT/logs/data_polling_<YYYYMMDD_HHMMSS>.log` by `cron/data_polling.sh`.  
The directory is created with `mkdir -p` so no pre-setup needed — only write permission on `OUTPUT/` is required.

### Lock File
`OUTPUT/.data_polling.lock` — prevents concurrent runs. If a stale lock exists after a crash, delete it manually before the next run.

---

## 5. Run Inputs

| Parameter | CLI flag | Example |
|---|---|---|
| Command | positional | `obs` |
| Start time | `--sd` | `09:00:00` |
| End time | `--ed` | `11:59:59` |
| Yesterday mode | `--ys` | (flag, no value) |

Environment (production vs staging) is determined automatically from the device CSV — the `environment` column in `device_list_config_mapped.csv` set by `device_data_setup/main_device_setup.py`. The obs DB (`PROD_OBS_DB` vs `STAG_OBS_DB`) is chosen accordingly in `DataProcessor.process_data()`.

**Device list source:**  
- If `OUTPUT/polling/<date>/device_data_*.csv` files already exist from a prior `extract` run, they are reused.  
- If the folder is missing, `daily_device_extraction()` runs automatically and generates them from the latest OTA versions in S3.  
- To force a specific device for a smoke run, manually place a minimal CSV in `OUTPUT/polling/<today>/device_data_smoke.csv` with columns: `Device_ID`, `environment`, `start_date`, `end_date` (the last two are stamped by the obs population step, so any placeholder value works).

---

## 6. Success Criteria & Duplicate Behaviour

### Expected success
- Rows appear in `extracteddata` for the device(s) in the test window.
- `extracteddata_registry` has an entry for each processed device with the date range covered.
- Log shows `"Successfully inserted N records for device <id>"` and `"Registry updated for device <id>"`.

### Duplicate run behaviour
Re-running the same window is safe:
1. `subtract_date_range_main` reads `extracteddata_registry` and returns an empty range → `fetch_s3_path` returns `[]` → device is skipped before any S3 call.
2. If the registry check is bypassed somehow, the insert uses `ON CONFLICT (s3_path, start_time) DO NOTHING`, so no duplicate rows are written to `extracteddata`.

Both guards must be intact for idempotency. Do not truncate `extracteddata_registry` between runs unless you intend a full re-ingest.
