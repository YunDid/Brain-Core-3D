# log_manager.py
import os
import datetime
from loguru import logger

class LogManager:
    """日志管理类，用于配置和初始化日志"""

    def __init__(self, log_dir="log"):
        self.log_dir = log_dir
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.log_file_path = os.path.join(self.log_dir, f"record.{timestamp}.log")
        self.setup_logging(self.log_file_path)

    def setup_logging(self, log_file_path):
        """设置日志存储"""
        log_folder = os.path.dirname(log_file_path)
        os.makedirs(log_folder, exist_ok=True)
        logger.add(log_file_path, rotation="100 MB")  # Rotates the log file when it reaches 100MB

    @staticmethod
    def get_logger():
        """返回logger实例"""
        return logger
