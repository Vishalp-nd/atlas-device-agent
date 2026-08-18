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

    def __init__(self, server: str = 'stag', filetype: str = 'log', dd=False, count=False):
        '''
            server might be prod, stag
            filetype might be log, healthstat, lla.
            dd --> Don't Download
        '''
        self.corrupted = {}
        self.filetype = filetype
        self.deviceid = None
        self.dates = None
        self.bt3_client = boto3.resource("s3")
        if server == 'prod':
            self.server = 'idms-production'
        else:
            self.server = 'idms-staging'
        self.dd = dd
        self.bucket = None
        self.dest_dir = None
        self.count = count

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
            destFile = 'log/' + '/'.join(s3file.key.split('/')[1:])
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
        if self.filetype == 'log':
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
        log_dir = "log/" + device
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


    Downloader(server, filetype=args.filetype, dd=False).download_manager(deviceid, args.startdate, args.enddate)
    # if args.filetype == "log":
    #     print("[!!] Downloading completed... Chronological analyzer Started...")
    #     ChronologicalAnalyzer().run(deviceid.split(','))

if __name__ == '__main__':
    main()
