import logging
import os


class Logger:
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        if self.logger.handlers:
            return

        self.logger.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        self.logger.addHandler(stream_handler)

        log_dir = os.path.join(os.getcwd(), "logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
            file_handler = logging.FileHandler(os.path.join(log_dir, f"{name}.log"))
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
        except OSError:
            # Keep stdout logging even when file-system permissions are restricted.
            pass

    def log_debug(self, message: str):
        self.logger.debug(message)

    def log_info(self, message: str):
        self.logger.info(message)

    def log_warning(self, message: str):
        self.logger.warning(message)

    def log_error(self, message: str):
        self.logger.error(message)

    def log_critical(self, message: str):
        self.logger.critical(message)
