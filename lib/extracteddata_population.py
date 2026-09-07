from pathlib import Path

import pandas as pd

from lib.logger import Logger
from lib.s3_manager import S3Manager
from pipeline.observation_extraction import DataProcessor


logger = Logger("extracteddata_population")


def _product_line_from_device_data_path(device_data: str) -> str:
    path = Path(device_data).resolve()
    parts = path.parts

    try:
        output_index = parts.index('OUTPUT')
    except ValueError as exc:
        raise ValueError(f"Could not derive product_line from path without OUTPUT segment: {device_data}") from exc

    if output_index + 1 >= len(parts):
        raise ValueError(f"Could not derive product_line from path: {device_data}")

    product_line = parts[output_index + 1]
    if product_line in {'polling', 'trigger', ''}:
        raise ValueError(f"Derived invalid product_line '{product_line}' from path: {device_data}")

    return product_line


def obs_processor(device_data: str, trigger_id: int) -> None:
    df = pd.read_csv(device_data)
    if df.empty:
        logger.log_warning(f"No rows found in {device_data}")
        return

    df = df.copy()
    if 'product_line' in df.columns and df['product_line'].notna().any():
        df['product_line'] = df['product_line'].fillna(method='ffill').fillna(method='bfill')
    else:
        df['product_line'] = _product_line_from_device_data_path(device_data)

    s3_manager = S3Manager()
    with DataProcessor(s3_manager, str(trigger_id)) as processor:
        s3_dict = processor.process_data(df)
        s3_dict = {k: v for k, v in s3_dict.items() if v}
        processor.insert_data_to_db(s3_dict)

    logger.log_info(f"Obs population complete for {device_data}")
