import datetime
import os
import re
import sys
from configparser import ConfigParser
import pandas as pd
import numpy as np
import math


class GlobalSettings:
    debug_pattern = False
    master_pattern = 'master_pattern'

class TimeRelated:
    @classmethod
    def make_timestamp_timezone_unaware(
        cls,
        timestamp: datetime.datetime
    ) -> datetime.datetime:
        return timestamp.replace(tzinfo=None)
    

    @classmethod
    def get_utc_ts_from_filename(
            cls,
            filename: str=None):
        ts = int(filename.split('_')[6])
        return datetime.datetime.utcfromtimestamp(ts//1000)

    @classmethod
    def convert_epoch_to_utc(cls, epoch:int):
        if not math.isnan(epoch):
            return datetime.datetime.utcfromtimestamp(epoch//1000)

    @classmethod
    def convert_epoch_to_utc_v2(cls, epoch:int)->datetime.datetime:
        if not math.isnan(epoch):
            raw_epoch = str(epoch)
            try:
                sanitized = int(raw_epoch[:10])
                return datetime.datetime.utcfromtimestamp(sanitized)
            except:
                print(raw_epoch)
                return pd.NaT
        else:
            return pd.NaT
    
    @classmethod
    def time_diff_to_seconds(cls, time_diff)->int:
        try:
            return time_diff.total_seconds()
        except ValueError:
            return math.nan
    
    @classmethod
    def convert_UTC_to_epoch(cls, timestamp):
        try:
            time_format = "%Y-%m-%d %H:%M:%S"
            naive_timestamp = datetime.datetime.strptime(timestamp[:19], time_format)
            epoch_date = datetime.datetime.strptime('1970-01-01 00:00:00', time_format)
            epoch = (naive_timestamp - epoch_date).total_seconds()
            epoch = epoch * 1000
            epoch += int(timestamp[20:23])
        except ValueError:
            epoch = 0
        return (int)(epoch)
    
    @classmethod
    def uptime_convertor(self, sec):
        if math.isnan(sec):
            return "NaT"
        sec = sec//1000
        Hours = sec//3600
        minute = sec%3600//60
        second = sec%60
        return "{0:02d}:{1:02d}:{2:02d}".format(int(Hours), int(minute), int(second))

class DataFrameRelated:
    @classmethod
    def drop_column_if_exist(
        cls,
        dfobj: pd.DataFrame,
        columns: list,
    ) -> pd.DataFrame:
        if columns is not None:
            for column in columns:
                if column in dfobj.columns.to_list():
                    dfobj.drop(column, axis=1, inplace=True)
            return dfobj
        else:
            try:
                Ex = ValueError()
                Ex.strerror = "columns argument should no be NoneType."
                raise Ex
            except ValueError as e:
                print("ValueError Exception!", e.strerror)
    
    @classmethod
    def add_column_if_not_exist(
        cls,
        dfobj: pd.DataFrame,
        columns: dict = None,
    ) -> pd.DataFrame:
        if columns is not None:
            for column, value in columns.items():
                if column not in dfobj.columns.to_list():
                    dfobj[column] = value
            return dfobj
        else:
            try:
                Ex = ValueError()
                Ex.strerror = "columns argument should no be NoneType."
                raise Ex
            except ValueError as e:
                print("ValueError Exception!", e.strerror)

def get_devices(deviceid):
    if 'str' in str(type(deviceid)):
        device_list = []
        if ',' in deviceid:
            device_list = deviceid.split(',')
        else:
            device_list.append(deviceid)
        return set(device_list)
    elif 'list' in str(type(deviceid)):
        return set(deviceid)


def get_time_stamp_from_string(time_string: str):
    date_time_obj = datetime.datetime.strptime(time_string, '%Y-%m-%d %H:%M:%S')
    return date_time_obj


def get_cassandra_bucket_id_list(start_time: str, end_time: str) -> list:
    """
    @param 
    start_time: start time in string time_format
    end_time: end time in string time_format

    @return
    bucket_id: list of bucket ids
    """
    start_time_obj = get_time_stamp_from_string(start_time)
    end_time_obj = get_time_stamp_from_string(end_time)
    bucket_id = []
    while True:
        if start_time_obj <= end_time_obj:
            bucket_id.append(
                start_time_obj.isocalendar()[0] * 100 + start_time_obj.isocalendar()[1]
                    )
            start_time_obj += datetime.timedelta(days=7)
            continue
        elif start_time_obj > end_time_obj:
            bucket_id.append(
                    end_time_obj.year * 100 + end_time_obj.isocalendar()[1]
                    )
            break
    return bucket_id

def get_dates(start_date, end_date):
    try:
        start = datetime.datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.datetime.strptime(end_date, "%Y-%m-%d")
        if start > end:
            print("[-] 'sd' must be less than or equal to 'ed'")
            exit(1)
        date_list = [(start + datetime.timedelta(days=x)).strftime("%Y-%m-%d")
                     for x in range(0, (end - start).days + 1)]
        return date_list
    except ValueError:
        print("[-] Entered date is invalid!!!")
        exit(1)


def uptime_convertor(sec):
    if sec is None:
        return 'NaT'
    if math.isnan(sec):
        return 'NaT'
    sec = sec//1000
    Hours = sec//3600
    minute = sec % 3600//60
    second = sec % 60
    return "{0:02d}:{1:02d}:{2:02d}".format(Hours, minute, second)


def config(config_file='/data/logreport/databases.ini', section=''):
    parser = ConfigParser()
    parser.optionxform = str
    parser.read(config_file)
    if section in parser:
        params = {k: v for k, v in parser.items(section)}
        return params
    else:
        print(f'[-] Section not available in {config_file}')
        sys.exit(1)


def get_devices_from_tenant(tenantlist, tenant):
    if os.getenv('machine', 'laptop') == 'jenkins':
        tenant_devices = re.findall(r'{0} .*'.format(tenant), tenantlist)
        deviceid = [re.search(r'\d{8,12}', device).group()
                    for device in tenant_devices]
        devices = ','.join(deviceid)
        if devices:
            return devices
        else:
            return None
    else:
        return None


if __name__ == "__main__":
    # config_reader('/home/bharathk/Desktop/source-codes/critical-info/tenantconfig.ini')
    print(get_cassandra_bucket_id_list('2023-01-01 00:00:00', '2023-08-28 00:00:00'))
