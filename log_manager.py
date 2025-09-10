# log_manager.py - 改进版本（基于 loguru）
import os
import datetime
from loguru import logger

class LogManager(object):
    """日志管理类，用于配置和初始化日志"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls, *args, **kwargs):
        """单例模式，确保只有一个 LogManager 实例"""
        if cls._instance is None:
            cls._instance = super(LogManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self, log_dir="log", log_level="INFO", console_output=True):
        # 避免重复初始化
        if self._initialized:
            return
            
        self.log_dir = log_dir
        self.log_level = log_level
        self.console_output = console_output
        
        # 生成带时间戳的日志文件名
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.log_file_path = os.path.join(self.log_dir, "record.{}.log".format(timestamp))
        
        self._setup_logging()
        self._initialized = True
    
    def _setup_logging(self):
        """设置日志配置"""
        # 清除默认处理器
        logger.remove()
        
        # 创建日志目录
        os.makedirs(self.log_dir, exist_ok=True)
        
        # 添加文件处理器
        logger.add(
            self.log_file_path,
            rotation="100 MB",
            retention="30 days",  # 保留30天的日志
            level=self.log_level,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[module_name]}:{function}:{line} - {message}",
            encoding="utf-8"
        )
        
        # 添加控制台处理器（可选）
        if self.console_output:
            logger.add(
                lambda msg: print(msg, end=""),  # 使用 lambda 避免重复输出
                level=self.log_level,
                format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{extra[module_name]}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
            )
    
    @staticmethod
    def get_logger(name=None):
        """
        获取 logger 实例
        
        Args:
            name: 日志记录器名称，通常是模块名
            
        Returns:
            配置好的 logger 实例
        """
        if name:
            return logger.bind(module_name=name)
        return logger.bind(module_name="Unknown")
    
    def set_level(self, level):
        """动态修改日志级别"""
        self.log_level = level
        # 重新配置日志
        self._setup_logging()
    
    def get_log_file_path(self):
        """获取当前日志文件路径"""
        return self.log_file_path

# 使用示例
if __name__ == "__main__":
    # 初始化日志管理器
    log_mgr = LogManager(log_level="DEBUG", console_output=True)
    
    # 获取不同模块的 logger
    main_logger = log_mgr.get_logger("main")
    file_logger = log_mgr.get_logger("file_processor")

    main_logger.info("程序启动")
    file_logger.debug("处理文件: test.dat")
    main_logger.warning("这是一个警告")
    main_logger.error("这是一个错误")