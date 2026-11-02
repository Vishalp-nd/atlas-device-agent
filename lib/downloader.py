import argparse
import boto3
import re
import concurrent.futures
from multiprocessing import Process, Pool
import extractor, utility
from logsize import LogSize
import time
import os
from pathlib import Path
from datetime import datetime
from botocore.credentials import InstanceMetadataProvider, InstanceMetadataFetcher
from botocore.session import Session as BotocoreSession

# from ndutility.dbaccess import DB

def timer(func):
    def wrap_func(*args, **kwargs):
        t1 = time.time()
        result = func(*args, **kwargs)
        t2 = time.time()
        print(f'Function {func.__name__!r} executed in {(t2-t1):.4f}s')
        return result
    return wrap_func
class Downloader:
    """
    Downloader class  -> Which is used to download file from s3
    """

    def __init__(self, server: str = 'stag', filetype: str = 'log', dd=False, count=False, extract_logs=True,
                 log_root=os.path.join('OUTPUT', 'log')):
        '''
            server might be prod, stag
            filetype might be log, healthstat, lla.
            dd --> Don't Download
            extract_logs --> merge the downloaded archives into <day>/logs. With False only
                the archives are kept (~17x smaller); lib/ota_report.py and
                lib/cfg_parser_logs.py read them directly (lib/log_archive.py)
            log_root --> where filetype 'log' goes, as <log_root>/<device>/<date>/
        '''
        self.corrupted = {}
        self.filetype = filetype
        self.deviceid = None
        self.dates = None
        self.bt3_client = self._build_session().resource("s3")
        if server == 'prod':
            self.server = 'idms-production'
        else:
            self.server = 'idms-staging'
        self.dd = dd
        self.bucket = None
        self.dest_dir = None
        self.count = count
        self.extract_logs = extract_logs
        self.log_root = log_root

    def _build_session(self):
        if self._is_running_on_ec2():
            session = self._build_ec2_session()
            if session is not None:
                return session
        return boto3.Session()

    def _is_running_on_ec2(self) -> bool:
        return os.path.exists('/sys/hypervisor/uuid') or os.path.exists('/sys/devices/virtual/dmi/id/product_uuid')

    def _build_ec2_session(self):
        try:
            botocore_session = BotocoreSession()
            fetcher = InstanceMetadataFetcher(
                timeout=float(os.getenv("AWS_METADATA_SERVICE_TIMEOUT", "1")),
                num_attempts=int(os.getenv("AWS_METADATA_SERVICE_NUM_ATTEMPTS", "2")),
            )
            provider = InstanceMetadataProvider(
                iam_role_fetcher=fetcher,
            )
            creds = provider.load()
            if creds is None:
                return None

            frozen = creds.get_frozen_credentials()
            return boto3.Session(
                aws_access_key_id=frozen.access_key,
                aws_secret_access_key=frozen.secret_key,
                aws_session_token=frozen.token,
                region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-west-1",
            )
        except Exception:
            return None

    def __directory_generator(self, device: str, date: str):
        '''
            will generate subdirectory for s3
        '''
        log_folder_list = ['logs_0', 'logs_1', 'logs_2', 'logs_3', 'logs_4']
        payload_folder_list = ['payloads_0', 'payloads_1',
                               'payloads_2', 'payloads_3', 'payloads_4']
        folder_list = []
        if self.filetype == 'log':
            for logfolder in log_folder_list:
                folder_list = folder_list + \
                    ['/'.join([logfolder, device, date])]
        if self.filetype == 'healthstat':
            for payload in payload_folder_list:
                folder_list = folder_list + \
                    ['/'.join([payload, 'upload-device-status', device, date])]
        if self.filetype == 'videolist':
            for payload in payload_folder_list:
                folder_list = folder_list + \
                    ['/'.join([payload, 'upload-videolist', device, date])]
        if self.filetype == 'lla':
            for payload in payload_folder_list:
                folder_list = folder_list + \
                    ['/'.join([payload, 'upload-lla', device, date])]
        return tuple(folder_list)

    def __get_content(self, device, date):
        """
            get content for the date of the particular device.
        """
        bucket = self.bt3_client.Bucket(self.server)
        file_list = []
        for directory in self.__directory_generator(device, date):
            # print(directory)
            file_list = file_list + \
                list(bucket.objects.filter(Prefix=directory))
        return file_list

    def __boto_download(self, s3file):
        destFile = None
        if self.filetype == 'log':
            destFile = os.path.join(self.log_root, *s3file.key.split('/')[1:])
        else:
            destFile = '/'.join(s3file.key.split('/')[1:])
        
        self.dest_dir = os.path.dirname(destFile)
        
        try:
            if not os.path.exists(self.dest_dir):
                os.makedirs(self.dest_dir)
        except FileExistsError:
            pass

        if os.path.exists(destFile):
            pass
        else:
            self.bucket.download_file(s3file.key, destFile)

    def extract(self, device, date,dest, keep_extract=True):
        # print('Downloading completed... Extraction Started...')
        retries = 0
        if self.corrupted.get(device) is not None:
            retries = int(self.corrupted[device].split(',')[-1])
        x = extractor.Extract(dest, keep_extract)
        status = x.mergelog()
        if status == 1 and retries < 3:
            self.corrupted[device] = date + ',' + str(retries+1)
            self.download_content(device, date)
        elif retries >= 3:
            print(f'{device} and {date} logs are corrupeted .. skipping')
        # print('Extraction completed')

    def download_content(self, device, date):
        """
            To download s3 files and folders
        """
        t = None
        if self.filetype == 'log':
            existing_dir = os.path.join(self.log_root, device, date)
            if os.path.isdir(existing_dir):
                print(f"{device} : {date} ==> skipped (already exists)")
                self.dest_dir = existing_dir
                return existing_dir
        # device = devicedate[0]
        # date = devicedate[1]
        s3files = self.__get_content(device, date)
        s3files_count = len(s3files)
        if self.count == True:
            print("{0} : {1} ==> {2}".format(device, date, s3files_count))
            return
        print("{0} : {1} ==> {2}".format(device, date, s3files_count))
        if s3files_count == 0:
            return
            
            
        
        # t1 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            executor.map(self.__boto_download, s3files)
        # t2 = time.perf_counter()
        # print(f'Finished in {round(t2-t1, 2)} seconds')
        if self.filetype == 'log' and self.extract_logs:
            self.extract(device, date, self.dest_dir, False)
        return self.dest_dir

    def download_manager(self,  deviceid: list, startdate, enddate):
        self.deviceid = utility.get_devices(deviceid)
        # if self.filetype == 'observation':
        #     for device in self.devices:
        #         print(DB().get_obs_zip_s3_path(device, startdate, enddate))
        #     return 0
        self.dates = utility.get_dates(startdate, enddate)
        
        # if True:
        #     log = LogSize(self.deviceid, self.dates, self.server)
        #     log.start()
        #     return 0
        processlist = []
        t1 = time.perf_counter()
        throttle = 8
        for device in self.deviceid:
            for date in self.dates:
                if self.dd == False:
                    processlist.append((device, date))
        
        framelist = []
        processesframe = []
        for p in processlist:
            processesframe.append(p)
            if len(processesframe) == throttle:
                framelist.append(processesframe.copy())
                processesframe.clear()
        framelist.append(processesframe.copy())
        processesframe.clear()
        
        for frame in framelist:
            plist = []
            self.bucket = self.bt3_client.Bucket(self.server)
            for t in frame:
                p = Process(target=self.download_content, args=t)
                p.start()
                plist.append(p)
            for p in plist:
                p.join()
            self.bucket = None
            time.sleep(1)

        t2 = time.perf_counter()
        print(f'Finished in {round(t2-t1, 2)} seconds')
        return self

class ChronologicalAnalyzer():
    def __init__(self):
        self.encoding_rules = {
                "default": "iso8859-15",
                "diagnostic": "utf-8",
                "diagnostic_c": "utf-8",
                "service_mon": "utf-8",
                "service_mon_c": "utf-8",
            }
        # Regex for datetime: e.g., 2023-11-17 22:20:32,775
        self.datetime_regex = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})')
        # Regex for epoch in milliseconds: 13 digits
        self.epoch_regex = re.compile(r'^(\d{13})(?!\d)')

    def run(self,device_list):
        print("Starting chronological analysis...")
        print(device_list)
        with concurrent.futures.ThreadPoolExecutor() as executor:
            executor.map(self.chronological_analyzer,device_list)
            
    def extract_timestamp_and_line(self,line):
        """Extracts timestamp and converts it to datetime object, returns remaining log line."""
        dt_match = self.datetime_regex.match(line)
        if dt_match:
            try:
                ts = datetime.strptime(dt_match.group(1), "%Y-%m-%d %H:%M:%S,%f")
                return ts, line[len(dt_match.group(1)):].strip()
            except ValueError:
                pass

        epoch_match = self.epoch_regex.match(line)
        if epoch_match:
            try:
                ts = datetime.fromtimestamp(int(epoch_match.group(1)) / 1000.0)
                return ts, line[len(epoch_match.group(1)):].strip()
            except (OSError, ValueError):
                pass

        return None, line.strip()  # No timestamp found
    
    @timer
    def chronological_analyzer(self,device):
        log_entries = []
        current_entry = None
        #time.sleep(20)
        print(f"Processing device: {device}")
        log_dir = os.path.join('OUTPUT', 'log', device)
        for file_path in Path(log_dir).rglob("*.log"):
            relative_path = file_path.relative_to(log_dir)
            folder_name = relative_path.parts[0] if len(relative_path.parts) > 1 else "."
            #print(f"Processing file: {file_path} in folder: {folder_name}")
            encoding = self.encoding_rules.get(folder_name, self.encoding_rules["default"])
            #print("########",current_entry)
            if "tar.gz" in str(file_path):
                print(f"Skipping compressed file: {file_path}")
                continue
            with open(file_path, 'r', encoding=encoding) as f:
                for line in f:
                    line = line.rstrip('\n')
                    ts, rest_of_line = self.extract_timestamp_and_line(line)
                    #print("Timestamp:", ts) 
                    if ts:
                        formatted_ts = ts.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
                        current_entry = (ts, folder_name, file_path.name, f"{formatted_ts} {rest_of_line}")
                        #print(rest_of_line)
                        if rest_of_line!= None and rest_of_line != '':
                            #print("hi")
                            log_entries.append(current_entry)
                    else:
                        #print(f"Line without timestamp: {current_entry}")
                        if current_entry:
                            current_entry = (
                                current_entry[0],
                                current_entry[1],
                                current_entry[2],
                                current_entry[3] + '\n' + rest_of_line
                            )
                            log_entries[-1] = current_entry

        print(f"Found {len(log_entries)} log entries in {log_dir}")
        log_entries.sort(key=lambda x: x[0])
        os.makedirs("chronological_logs", exist_ok=True)
        output_file = f"chronological_logs/{device}_merged_logs.txt"

        with open(output_file, 'w') as out:
            for _, folder, file_name, log_line in log_entries:
                out.write(f"{file_name} | {log_line}\n")

        print(f"All logs merged and written to: {output_file}")

class OTADownloader:
    """
    Download an OTA package from s3, pull out the requested files into
    INPUT/<ota_version>/ and delete the downloaded package.

    D475 (9.x) packages live in a different bucket as nd_app_setup.tar.gz,
    with the actual OTA tarball nested inside under ota_packages/.
    """

    INPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'INPUT')
    BUCKETS = ('idms-staging', 'idms-production')

    # Major version (or the number after ".sp." for sp builds) -> s3 product folder
    PRODUCT_BY_MAJOR = {
        '2': 'krait',
        '3': 'bagheera2',
        '4': 'krait2',
        '5': 'bagheera3',
        '7': 'octo',
        '9': 'D475'
    }

    # Products whose packages are not under <bucket>/ota_packages/<product>/
    PRODUCT_S3_PATHS = {
        'D475': 's3://netradyne-sharing/Products/Qualcomm_D475/Unified_OS_OTA/{ota_version}/nd_app_setup.tar.gz',
    }

    def __init__(self, ota_version, product_name=None, s3_path=None):
        self.ota_version = ota_version
        self.product_name = product_name or self.product_from_version(ota_version)
        self.s3_path = s3_path
        self.dir_name = os.path.join(self.INPUT_DIR, ota_version)

    @classmethod
    def product_from_version(cls, ota_version):
        sp = re.search(r'\.sp\.(\d+)\.', ota_version)
        major = sp.group(1) if sp else ota_version.split('.')[0]
        if major not in cls.PRODUCT_BY_MAJOR:
            raise ValueError(f'Cannot infer product for OTA {ota_version}, pass product_name')
        return cls.PRODUCT_BY_MAJOR[major]

    @staticmethod
    def get_bucket_key(s3_path):
        bucket = s3_path.split('/')[2].replace('amazonaws.com', '')
        key = s3_path.split('/')[3:]
        key = '/'.join(key)

        return bucket, key

    def download(self):
        s3client = boto3.client('s3')

        s3_path = self.s3_path or self.PRODUCT_S3_PATHS.get(self.product_name, '').format(ota_version=self.ota_version)
        if s3_path:
            candidates = [self.get_bucket_key(s3_path)]
        else:
            # Older OTAs are package.tar.gz, newer ones package.7z
            candidates = [(bucket, f'ota_packages/{self.product_name}/{self.ota_version}/{name}')
                          for bucket in self.BUCKETS for name in ('package.tar.gz', 'package.7z')]

        for bucket, s3_file_path in candidates:
            local_file = os.path.join(self.dir_name, os.path.basename(s3_file_path))
            if os.path.exists(local_file) and os.path.getsize(local_file) > 0:
                return local_file
            try:
                s3client.head_object(Bucket=bucket, Key=s3_file_path)
            except s3client.exceptions.ClientError:
                continue
            print(f'[+] Downloading s3://{bucket}/{s3_file_path}')
            os.makedirs(self.dir_name, exist_ok=True)
            s3client.download_file(bucket, s3_file_path, local_file)
            return local_file

        raise FileNotFoundError(
            f'OTA package not found: ' + ', '.join(f's3://{b}/{k}' for b, k in candidates))

    def fetch_files(self, files=('bagheera_config.ini',)):
        """Download the package, keep only `files` in INPUT/<ota_version>/, delete the rest."""
        local_file = self.download()
        try:
            return extractor.Extract.extract_ota(local_file, files, self.dir_name)
        finally:
            if os.path.exists(local_file):
                os.remove(local_file)


def args_for_downloader():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-i', '--deviceid', help='[required]Provide device id or to provide multiple id use comma to separate id')
    parser.add_argument(
        '-l', '--list', help='Provide  text file contains device list')
    parser.add_argument(
        '-t', '--tenant', help='Provide  Tenant name in the file devicelist.txt')
    parser.add_argument('-sd', '--startdate',
                        help='Provide start date in yyyy-mm-dd', required=True)
    parser.add_argument('-ed', '--enddate',
                        help='Provide end date in yyyy-mm-dd', required=True)
    parser.add_argument(
        '-f', '--filetype', help='Provide file type it may be log, healthstat, lla', required=True)
    parser.add_argument(
        "-p", help="point to production server", action="store_true")
    parser.add_argument(
        "--count", help="Return Log count without downloading log", action="store_true")
    parser.add_argument(
        "--no-extract", help="Keep only the downloaded log archives, don't merge them into <day>/logs",
        action="store_true")

    return parser.parse_args()

def main():
    args = args_for_downloader()
    server = 'stag'
    if args.p == True:
        server = 'prod'

    deviceid = None
    if args.deviceid is not None:
        deviceid = args.deviceid

    if args.list is not None:
        with open(args.list) as devicelist:
            deviceid = devicelist.read().replace('\n', ',').strip(',')
            # print(deviceid)
    if args.tenant is not None:
        with open('devicelist.txt') as devicelist:
            tenants = devicelist.read()
        tenant = re.findall(r'{0}.*'.format(args.tenant), tenants)
        deviceid = [re.search(r'\d{1,12}', device).group() for device in tenant]
        deviceid = ','.join(deviceid)
        print(deviceid)

    if deviceid is None:
        print('[-] Please Provide Device id')
        exit(1)

    if not any(f == args.filetype for f in ["log", "healthstat", "lla", 'videolist']):
        print("[-] Please provide proper filetype for -f parameter [ log|healthstat|lla ]")
        exit(1)

    if args.count == True:
        print("[!!] Displaying the {0} count for the given date range.".format(
            args.filetype))
        Downloader(server, filetype=args.filetype, dd=False, count=True).download_manager(
            deviceid, args.startdate, args.enddate)
        exit(0)


    Downloader(server, filetype=args.filetype, dd=False, extract_logs=not args.no_extract).download_manager(
        deviceid, args.startdate, args.enddate)
    # if args.filetype == "log":
    #     print("[!!] Downloading completed... Chronological analyzer Started...")
    #     ChronologicalAnalyzer().run(deviceid.split(','))

if __name__ == '__main__':
    main()
