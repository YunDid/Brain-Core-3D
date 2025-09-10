import os
from log_manager import LogManager

class FileInfo(object):
    """文件信息类"""
    def __init__(self, filename, basename, file_type, file_descriptor=None):
        self.filename = filename
        self.basename = basename
        self.file_type = file_type
        self.file_descriptor = file_descriptor

class FileProcessor(object):
    """
    专门负责文件分类和管理的类
    职责：识别文件类型，管理文件描述符
    """
    
    def __init__(self):
        self.current_directory = None  # type: str
        self.files = {}  # type: dict[str, FileInfo]
        self._logger = LogManager.get_logger("FileProcessor")
        # 计数器 计数不同类型的文件个数
        self.file_counts_by_type = {}
        # 构建缓存，不然每次轮询 files 取对应类型的所有文件 性能太低
        self.files_by_type = {
            'timestamp': [],
            'amp': [],
            'stim': [],
            'digital_in': [],
            'info': []
        }
        
        # 文件类型识别规则，违反开闭，但是因为变动不多，其实没必要再使用注册机制进行轮询了
        self.file_patterns = {
            'timestamp': lambda name: 'time.dat' in name,
            'info': lambda name: 'info.rhs' in name,
            'stim': lambda name: name.startswith('stim') and name.endswith('.dat'),
            'amp': lambda name: name.startswith('amp') and name.endswith('.dat'),
            'digital_in': lambda name: name.startswith('board-DIGITAL-IN') and name.endswith('.dat')
        }
        
    def process_new_file(self, filepath):
        """
        处理新文件，返回文件信息，主要功能就是存一个文件描述符列表，以及相关的状态量，方便后续对文件的读取
        
        Args:
            filepath: 文件完整路径
            
        Returns:
            FileInfo 对象或 None
        """
        
        if filepath in self.files:
            return self.files[filepath]  # 已处理过，直接返回

        directory = os.path.dirname(filepath)
        basename = os.path.basename(filepath)
        
        # 检查是否需要切换目录
        if self.current_directory != directory:
            self._switch_directory(directory)
            
        # 识别文件类型
        file_type = self._identify_file_type(basename)
        if not file_type:
            self._logger.debug("Unknown file type: {}", basename)
            return None
            
        # 创建文件信息
        file_info = FileInfo(
            filename=filepath,
            basename=basename,
            file_type=file_type
        )
        
        # 尝试打开文件
        try:
            file_info.file_descriptor = open(filepath, 'rb')
            self.files[filepath] = file_info
            # 直接添加到对应类型的列表 - 缓存
            self.files_by_type[file_type].append(file_info)
            # 更新计数器
            count = self.file_counts_by_type.get(file_type, 0)
            self.file_counts_by_type[file_type] = count + 1
            
            self._logger.info("Added {} file: {}", file_type, basename)
            return file_info
        except IOError as e:
            self._logger.error("Failed to open {}: {}", filepath, e)
            return None
            
    def _identify_file_type(self, basename):
        """识别文件类型"""
        for file_type, pattern_func in self.file_patterns.items():
            if pattern_func(basename):
                return file_type
        return None
        
    def _switch_directory(self, new_directory):
        """切换到新目录"""
        self._logger.info("Switching to directory: {}", new_directory)
        
        # 关闭所有现有文件
        closed_count = self.close_all_files()
        if closed_count > 0:
            self._logger.info("Closed {} files during directory switch", closed_count)
        
        # 清空类型文件计数器
        # 清空计数器
        self.file_counts_by_type.clear()
        
        # 更新当前目录
        self.current_directory = new_directory
        self.files.clear()
        
    def close_all_files(self):
        """关闭所有打开的文件"""
        closed_count = 0
        for file_info in self.files.values():
            if file_info.file_descriptor:
                try:
                    file_info.file_descriptor.close()
                    closed_count += 1
                except Exception as e:
                    self._logger.warning("Failed to close file {}: {}", file_info.filename, e)
        return closed_count
                    
    def get_files_by_type(self, file_type):
        """
        获取指定类型的所有文件
        
        Args:
            file_type: 文件类型字符串
            
        Returns:
            FileInfo 对象列表
        """
        return self.files_by_type.get(file_type, [])
        
    def get_file_count_by_type(self, file_type):
        """获取指定类型的文件数量"""
        return self.file_counts_by_type.get(file_type, 0)