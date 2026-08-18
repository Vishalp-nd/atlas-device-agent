import boto3
import sys
import datetime
import time
from dateutil.tz import tzutc
import pandas as pd

class LogSize:
    def __init__(self, devices, dates, timestamp, bucket='idms-staging', input_ds=None):
        '''
            LogSize class allows details of file object available in 
            s3 without downloading actual file
        '''
        self.devices=devices
        self.dates=dates
        self.timestamp = timestamp
        self.bucket=bucket
        self._input_ds = input_ds
        self.result = {}

    def summary(self):
        result_summary = []
        
        with pd.ExcelWriter( self._input_ds['output_dir'] +"/Log_Size_Summary.xlsx_{1}__{2}.xlsx".format(self.timestamp, self.dates[0], self.dates[-1])) as log_size_summary:
            logsummary = pd.read_excel(self._input_ds['output_dir'] + '/DeviceQA_LogsSummary_{1}_{2}.xlsx'.format(self.timestamp, self.dates[0], self.dates[-1]), sheet_name=None)
            with pd.ExcelWriter(self._input_ds['output_dir'] + '/DeviceQA_LogsSummary_{1}_{2}.xlsx'.format(self.timestamp, self.dates[0], self.dates[-1])) as writer:
                for s in logsummary:
                    if 'Unnamed: 0' in logsummary[s].columns:
                        logsummary[s].drop(columns='Unnamed: 0',inplace=True)
                    logsummary[s].set_index(logsummary[s].columns[0], inplace=True)
                    logsummary[s].to_excel(writer, s)

                for device in self.result:
                    master_df = pd.DataFrame(self.result[device])
                    if not master_df.empty:
                        for date in master_df['UploadedDate'].unique():
                            df = master_df[master_df['UploadedDate'] == date]
                            result_summary.append({
                                'Device': device,
                                'Date': date,
                                'No Of Zip Files': df.shape[0],
                                'Min File Size (KB)': round(df['Size (KB)'].min(), 2),
                                'Max File Size (KB)':round(df['Size (KB)'].max(), 2),
                                'Avg File Size (KB)':round(df['Size (KB)'].mean(), 2),
                                'Overall File Size (KB)' : round(df['Size (KB)'].sum(), 2),
                                'Overall File Size (MB)' : round(df['Size (KB)'].sum()/1024, 2),
                            })
                    master_df.to_excel(log_size_summary, device)

                summ = pd.DataFrame(result_summary)
                summ.to_excel(writer, 'Log_Size_Summary')
                summ.to_excel(log_size_summary, 'Log_Size_Summary')

    def start(self):
        s3_client = boto3.client('s3')
        for device in self.devices:
            device_log = []
            for date in self.dates: 
                file_object_list = s3_client.list_objects(Bucket=self.bucket, Prefix='logs_0/{0}/{1}/'.format(device, date))
                if file_object_list.get('Contents') is not None:
                    for file_object  in file_object_list['Contents']:
                        device_log.append({
                            'FileName': file_object['Key'], 
                            'Size (KB)':round(file_object['Size']/1024, 2), 
                            'LastModified': str(file_object['LastModified']), 
                            'UploadedDate': file_object['Key'].split('/')[2]
                        })
            self.result[device] = device_log
        self.summary()




def  main():
    log_size = LogSize(sys.argv[1], sys.argv[2])
    log_size.start()

if __name__ == '__main__':
    main()
