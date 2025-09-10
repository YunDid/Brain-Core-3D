import numpy as np
from abc import ABC, abstractmethod
import struct
import mmap
import os
import time
from log_manager import LogManager

class DataReader(ABC):
    """数据读取器的抽象基类"""
    
    def __init__(self, sample_rate=30000):
        self.sample_rate = sample_rate
        self.stored_samples = 0
        # 为每个文件维护mmap状态
        self.mmap_states = {}  # {file_path: {'mmap': mmap_obj, 'offset': int, 'mapped_end': int}}
        self.use_mmap = True  # 可以通过这个开关控制
        
    @abstractmethod
    def read(self, file_descriptor, num_samples):
        """读取数据的抽象方法"""
        pass
    
    def _read_from_mmap(self, file_descriptor, num_samples, dtype, bytes_per_sample):
        """从mmap读取增量数据"""
        file_path = file_descriptor.name
        bytes_needed = num_samples * bytes_per_sample
        
        # Windows要求offset是64KB的整数倍
        ALLOC_GRANULARITY = 65536  # 64KB
        
        if file_path not in self.mmap_states:
            # 首次映射时检查空文件
            if num_samples == 0:
                return np.array([], dtype=dtype)
            
            # 基于增量推算文件大小
            estimated_file_size = bytes_needed  # 首次读取，文件至少有这么大
            map_size = min(estimated_file_size, 1000 * 1024 * 1024)
            
            # 但首次映射还是需要getsize，确保不超出实际文件
            actual_file_size = os.path.getsize(file_path)
            map_size = min(map_size, actual_file_size)
            
            if map_size == 0:
                return np.array([], dtype=dtype)
            
            mm = mmap.mmap(file_descriptor.fileno(), map_size, access=mmap.ACCESS_READ)
            self.mmap_states[file_path] = {
                'mmap': mm,
                'offset': 0,
                'map_start': 0,
                'map_size': map_size
            }
        
        state = self.mmap_states[file_path]
        offset = state['offset']
        
        # 检查是否需要重新映射
        if offset + bytes_needed > state['map_start'] + state['map_size']:
            # 关闭旧映射
            state['mmap'].close()
            
            # 计算对齐的offset（向下取整到最近的64KB边界）
            aligned_offset = (offset // ALLOC_GRANULARITY) * ALLOC_GRANULARITY
            
            # 基于当前offset和增量推算文件最小大小
            estimated_file_size = offset + bytes_needed
            remaining = estimated_file_size - aligned_offset
            
            # 确保映射窗口足够大以包含要读取的数据
            offset_diff = offset - aligned_offset
            min_size = offset_diff + bytes_needed
            map_size = min(max(min_size, 1000 * 1024 * 1024), remaining)
            
            mm = mmap.mmap(file_descriptor.fileno(), map_size, 
                          offset=aligned_offset, access=mmap.ACCESS_READ)
            state['mmap'] = mm
            state['map_start'] = aligned_offset
            state['map_size'] = map_size
        
        # 计算在当前映射中的相对位置
        local_offset = offset - state['map_start']
        
        # 确保不超出映射边界
        actual_available = state['map_size'] - local_offset
        actual_samples = min(num_samples, actual_available // bytes_per_sample)
        actual_bytes = actual_samples * bytes_per_sample
        
        # 读取数据
        buffer = state['mmap'][local_offset:local_offset + actual_bytes]
        data = np.frombuffer(buffer, dtype=dtype, count=actual_samples)
        
        # 更新全局offset
        state['offset'] += actual_bytes
        
        return data.copy()
        
    def reset(self):
        """重置读取状态"""
        self.stored_samples = 0
        # 关闭所有mmap
        for state in self.mmap_states.values():
            state['mmap'].close()
        self.mmap_states.clear()

class TimestampReader(DataReader):
    """时间戳数据读取器"""
    
    def read(self, file_descriptor, num_samples):
        """读取时间戳数据"""
        if self.use_mmap == True:
            t_start = time.perf_counter()  # 开始时间点
            data = self._read_from_mmap(file_descriptor, num_samples, np.int32, 4)
            t_end = time.perf_counter()  # 结束时间点
            elapsed_ms = (t_end - t_start) * 1000  # 转换为毫秒
            self._logger.debug("mmap Timestamp read_delay {} ms", elapsed_ms)
        else:
            t_start = time.perf_counter()  # 开始时间点
            data = np.fromfile(file_descriptor, dtype=np.int32, count=num_samples)
            t_end = time.perf_counter()  # 结束时间点
            elapsed_ms = (t_end - t_start) * 1000  # 转换为毫秒
            self._logger.debug("fromfile Timestamp read_delay {} ms", elapsed_ms)
        self.stored_samples += len(data)
        return data / float(self.sample_rate)

class AmpDataReader(DataReader):
    """放大器数据读取器"""
    
    def __init__(self, sample_rate=30000, scale_factor=0.195):
        super(AmpDataReader, self).__init__(sample_rate)
        self.scale_factor = scale_factor
        
    def read(self, file_descriptor, num_samples):
        """读取放大器数据"""
        if self.use_mmap == True:
            t_start = time.perf_counter()  # 开始时间点
            data = self._read_from_mmap(file_descriptor, num_samples, np.int16, 2)
            t_end = time.perf_counter()  # 结束时间点
            elapsed_ms = (t_end - t_start) * 1000  # 转换为毫秒
            self._logger.debug("mmap Amp read_delay {} ms", elapsed_ms)
        else:
            t_start = time.perf_counter()  # 开始时间点
            data = np.fromfile(file_descriptor, dtype=np.int16, count=num_samples)
            t_end = time.perf_counter()  # 结束时间点
            elapsed_ms = (t_end - t_start) * 1000  # 转换为毫秒
            self._logger.debug("fromfile Amp read_delay {} ms", elapsed_ms)
        self.stored_samples += len(data)
        return data * self.scale_factor

class StimDataReader(DataReader):
    """刺激数据读取器"""
    
    def __init__(self, sample_rate=30000, stim_step_size=10):
        super(StimDataReader, self).__init__(sample_rate)
        self.stim_step_size = stim_step_size
        
    def read(self, file_descriptor, num_samples):
        """读取刺激数据"""
        if self.use_mmap == True:
            t_start = time.perf_counter()  # 开始时间点
            data = self._read_from_mmap(file_descriptor, num_samples, np.uint16, 2)
            t_end = time.perf_counter()  # 结束时间点
            elapsed_ms = (t_end - t_start) * 1000  # 转换为毫秒
            self._logger.debug("mmap Stim read_delay {} ms", elapsed_ms)
        else:
            t_start = time.perf_counter()  # 开始时间点
            data = np.fromfile(file_descriptor, dtype=np.uint16, count=num_samples)
            t_end = time.perf_counter()  # 结束时间点
            elapsed_ms = (t_end - t_start) * 1000  # 转换为毫秒
            self._logger.debug("fromfile Stim read_delay {} ms", elapsed_ms)
        self.stored_samples += len(data)
        
        current_magnitude = np.bitwise_and(data, 255) * self.stim_step_size
        sign = (128 - np.bitwise_and(data, 256)) / 128.0
        return current_magnitude * sign
        
    def read_withStatus(self, file_descriptor, num_samples):
        """读取刺激数据并返回状态信息"""
        t_start = time.perf_counter()  # 开始时间点
        data = self._read_from_mmap(file_descriptor, num_samples, np.uint16, 2)
        t_end = time.perf_counter()  # 结束时间点
        elapsed_ms = (t_end - t_start) * 1000  # 转换为毫秒
        self._logger.debug("mmap Stim read_withStatus read_delay {} ms", elapsed_ms)
        self.stored_samples += len(data)
        
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
        if self.use_mmap == True:
            t_start = time.perf_counter()  # 开始时间点
            data = self._read_from_mmap(file_descriptor, num_samples, np.uint16, 2)
            t_end = time.perf_counter()  # 结束时间点
            elapsed_ms = (t_end - t_start) * 1000  # 转换为毫秒
            self._logger.debug("mmap Digital read_delay {} ms", elapsed_ms)
        else:
            t_start = time.perf_counter()  # 开始时间点
            data = np.fromfile(file_descriptor, dtype=np.uint16, count=num_samples)
            t_end = time.perf_counter()  # 结束时间点
            elapsed_ms = (t_end - t_start) * 1000  # 转换为毫秒
            self._logger.debug("fromfile Digital read_delay {} ms", elapsed_ms)
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
        """获取指定类型的读取器"""
        return self.readers.get(file_type)
        
    def reset_all(self):
        """重置所有读取器"""
        for reader in self.readers.values():
            reader.reset()