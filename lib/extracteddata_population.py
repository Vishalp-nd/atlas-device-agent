import pandas as pd

from lib.logger import Logger
from lib.s3_manager import S3Manager
from pipeline.observation_extraction import DataProcessor


logger = Logger("extracteddata_population")


def obs_processor(device_data: str, trigger_id: int) -> None:
    df = pd.read_csv(device_data)
    if df.empty:
        logger.log_warning(f"No rows found in {device_data}")
        return

    s3_manager = S3Manager()
    with DataProcessor(s3_manager, str(trigger_id)) as processor:
        s3_dict = processor.process_data(df)
        s3_dict = {k: v for k, v in s3_dict.items() if v}
        processor.insert_data_to_db(s3_dict)

    logger.log_info(f"Obs population complete for {device_data}")
