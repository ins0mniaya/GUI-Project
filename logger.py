# logger.py
import logging
import os
from logging.handlers import RotatingFileHandler
from PySide6.QtCore import QObject, Signal
from config import LOG_CONFIG

class UILogSignaller(QObject):
    log_signal = Signal(str, str) 

class UILogHandler(logging.Handler):
    def __init__(self, signaller):
        super().__init__()
        self.signaller = signaller

    def emit(self, record):
        self.signaller.log_signal.emit(record.levelname, self.format(record))

def setup_global_logger(ui_signaller=None):
    """初始化全局日志配置。"""
    os.makedirs(LOG_CONFIG["log_dir"], exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(getattr(logging, LOG_CONFIG["log_level"]))

    if logger.handlers:
        logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%H:%M:%S'))
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        os.path.join(LOG_CONFIG["log_dir"], LOG_CONFIG["log_file"]),
        maxBytes=LOG_CONFIG["max_bytes"],
        backupCount=LOG_CONFIG["backup_count"],
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter('%(asctime)s - [%(name)s] - [%(levelname)s] - %(message)s'))
    logger.addHandler(file_handler)

    if ui_signaller:
        ui_handler = UILogHandler(ui_signaller)
        ui_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))
        logger.addHandler(ui_handler)