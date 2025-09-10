from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import os
from log_manager import LogManager

class FileMonitor(object):
    """
    专门负责文件系统监控的类
    职责：监控指定目录，发现新文件时通知外部
    """
    
    def __init__(self):
        self.observer = None  # type: Observer
        self.directory_to_monitor = None  # type: str
        self.is_running = False
        self._file_created_callback = None
        self._logger = LogManager.get_logger("FileMonitor")
        
    def set_file_created_callback(self, callback):
        """
        设置文件创建时的回调函数
        
        Args:
            callback: 接收文件路径的回调函数
        """
        self._file_created_callback = callback
        
    def start(self, directory):
        """
        开始监控指定目录
        
        Args:
            directory: 要监控的目录路径
        """
        if self.is_running:
            self.stop()
            
        self.directory_to_monitor = directory
        self.observer = Observer()
        event_handler = self._FileHandler(self._on_file_created)
        self.observer.schedule(event_handler, directory, recursive=True)
        self.observer.start()
        self.is_running = True
        self._logger.info("Started monitoring directory: {}", directory)
        
    def stop(self):
        """停止监控"""
        if self.is_running and self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer.unschedule_all()
            self.is_running = False
            self._logger.info("Stopped monitoring directory")
            
    def _on_file_created(self, file_path):
        """内部回调，转发给外部注册的回调，解耦的设计"""
        if self._file_created_callback:
            self._file_created_callback(file_path)
            
    class _FileHandler(FileSystemEventHandler):
        """内部类：处理文件系统事件"""
        def __init__(self, callback):
            self.callback = callback
            self._logger = LogManager.get_logger("FileHandler")
            
        def on_created(self, event):
            if event.is_directory:
                self._logger.debug("New directory created: {}", event.src_path)
                return
                
            if any(event.src_path.endswith(ext) for ext in ['.dat', '.rhs']):
                self.callback(event.src_path)