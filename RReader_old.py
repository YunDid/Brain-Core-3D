import numpy as np
from abc import ABC, abstractmethod
import struct
from log_manager import LogManager

class DataReader(ABC):
    """数据读取器的抽象基类"""
    
    def __init__(self, sample_rate=30000):
        self.sample_rate = sample_rate
        self.stored_samples = 0
        
    @abstractmethod
    def read(self, file_descriptor, num_samples):
        """读取数据的抽象方法"""
        pass
        
    def reset(self):
        """重置读取状态"""
        self.stored_samples = 0

class TimestampReader(DataReader):
    """时间戳数据读取器"""
    
    def read(self, file_descriptor, num_samples):
        """
        读取时间戳数据
        
        Args:
            file_descriptor: 文件对象
            num_samples: 要读取的样本数
            
        Returns:
            numpy数组，包含时间戳（秒）
        """
        # if hasattr(file_descriptor, 'seekable') and file_descriptor.seekable():
        #     file_descriptor.seek(self.stored_samples * 4)  # 32位整数
            
        data = np.fromfile(file_descriptor, dtype=np.int32, count=num_samples)
        self.stored_samples += len(data)
        
        # 转换为时间（秒）
        return data / float(self.sample_rate)

class AmpDataReader(DataReader):
    """放大器数据读取器"""
    
    def __init__(self, sample_rate=30000, scale_factor=0.195):
        super(AmpDataReader, self).__init__(sample_rate)
        self.scale_factor = scale_factor
        
    def read(self, file_descriptor, num_samples):
        """读取放大器数据"""
        # if hasattr(file_descriptor, 'seekable') and file_descriptor.seekable():
        #     file_descriptor.seek(self.stored_samples * 2)  # 16位整数
            
        data = np.fromfile(file_descriptor, dtype=np.int16, count=num_samples)
        self.stored_samples += len(data)
        
        # 应用缩放因子
        return data * self.scale_factor

class StimDataReader(DataReader):
    """刺激数据读取器
    
    这里注意对外接口是什么，read_with_status，后面看一下是不是需要改一下
    """
    
    def __init__(self, sample_rate=30000, stim_step_size=10):
        super(StimDataReader, self).__init__(sample_rate)
        self.stim_step_size = stim_step_size
        
    def read(self, file_descriptor, num_samples):
        """读取刺激数据"""
        # if hasattr(file_descriptor, 'seekable') and file_descriptor.seekable():
        #     file_descriptor.seek(self.stored_samples * 2)  # 16位无符号整数
            
        data = np.fromfile(file_descriptor, dtype=np.uint16, count=num_samples)
        self.stored_samples += len(data)
        
        # 解析刺激数据
        current_magnitude = np.bitwise_and(data, 255) * self.stim_step_size
        sign = (128 - np.bitwise_and(data, 256)) / 128.0
        return current_magnitude * sign
        
    def read_withStatus(self, file_descriptor, num_samples):
        """读取刺激数据并返回状态信息"""
        # if hasattr(file_descriptor, 'seekable') and file_descriptor.seekable():
        #     file_descriptor.seek(self.stored_samples * 2)
            
        data = np.fromfile(file_descriptor, dtype=np.uint16, count=num_samples)
        self.stored_samples += len(data)
        
        # 解析所有信息
        current_magnitude = np.bitwise_and(data, 255) * self.stim_step_size
        sign = (128 - np.bitwise_and(data, 256)) / 128.0
        
        return {
            'Stimdata': current_magnitude * sign,
            'compliance_limit': np.bitwise_and(data, 32768) != 0,
            'charge_recovery': np.bitwise_and(data, 16384) != 0,
            'amplifier_settle': np.bitwise_and(data, 8192) != 0
        }

class DigitalDataReader(DataReader):
    """数字输入数据读取器"""
    
    def read(self, file_descriptor, num_samples):
        """读取数字输入数据"""
        # if hasattr(file_descriptor, 'seekable') and file_descriptor.seekable():
        #     file_descriptor.seek(self.stored_samples * 2)  # 16位整数
            
        data = np.fromfile(file_descriptor, dtype=np.uint16, count=num_samples)
        self.stored_samples += len(data)
        
        return data

class DataReaderFactory(object):
    """创建合适的数据读取器"""
    
    def __init__(self, sample_rate=30000):
        self.sample_rate = sample_rate
        self.readers = {
            'timestamp': TimestampReader(sample_rate),
            'amp': AmpDataReader(sample_rate),
            'stim': StimDataReader(sample_rate),
            'digital_in': DigitalDataReader(sample_rate)
        }
        
    def get_reader(self, file_type):
        """
        获取指定类型的读取器
        
        Args:
            file_type: 文件类型字符串
            
        Returns:
            DataReader 实例或 None
        """
        return self.readers.get(file_type)
        
    def reset_all(self):
        """重置所有读取器"""
        for reader in self.readers.values():
            reader.reset()