import numpy as np
import os
import time
from threading import Thread

from PyQt5.QtCore import QThread
from log_manager import LogManager

# 导入重构后的模块
from file_monitor import FileMonitor
from file_processor import FileProcessor, FileInfo
from data_readers import DataReaderFactory
from circular_buffer import CircularBuffer


class RealTimeDataReader(QThread):
    """
    重构后的实时数据读取器
    现在只负责：协调其他组件，管理数据流，提供对外接口
    """
    
    def __init__(self):
        QThread.__init__(self, parent=None)
        
        # 初始化各个职责组件
        self.file_monitor = FileMonitor()
        self.file_processor = FileProcessor()
        self.reader_factory = DataReaderFactory()
        
        # 设置文件监控回调
        self.file_monitor.set_file_created_callback(self._on_new_file)
        
        # 数据缓冲相关（暂时保留）
        self.circular_buffer = CircularBuffer(1000)
        self.samples_per_100ms = None
        self.temp_data_t = None
        self.temp_data_d = None
        self.temp_data_s = None
        self.temp_data_di = None
        
        # 线程相关（暂时保留）
        self.loading_running = False
        self.data_loading_thread = None
        self.ready_to_load = False
        
        # 其他配置（暂时保留）
        self.sample_rate = 30000
        self.min_samples_per_read = 3000
        self.stored_samples = 0
        
        # 使用统一的日志管理器
        self._logger = LogManager.get_logger("RealTimeDataReader")
        
        # 启动数据加载线程
        self.start_data_loading_thread()
        
    def set_monitoring_directory(self, directory):
        """设置监控目录"""
        self._logger.info("Setting monitoring directory to: {}", directory)
        
        # 停止当前监控
        self.file_monitor.stop()
        
        # 停止数据加载
        self.ready_to_load = False
        
        # 清理文件处理器
        self.file_processor.close_all_files()
        
        # 重置读取器
        self.reader_factory.reset_all()
        
        # 清空缓冲区
        self.circular_buffer.clear()
        
        # 重置状态
        self.stored_samples = 0
        
        # 启动新的监控
        self.file_monitor.start(directory)
        
    def _on_new_file(self, filepath):
        """处理新文件的回调"""
        # 暂停数据加载
        self.ready_to_load = False
        
        # 处理文件
        file_info = self.file_processor.process_new_file(filepath)
        if not file_info:
            return
            
        # # 特殊处理info文件 TODO 这里采样率的读取我记得有问题 先不用
        # if file_info.file_type == 'info':
        #     # 目前主要从info里面读采样率 其他信息之后改 _read_sample_rate_from_info 这个接口
        #     self._read_sample_rate_from_info(file_info)
            
        # 检查是否可以开始加载数据
        self._check_ready_to_load()
        
    def _check_ready_to_load(self):
        """检查是否所有必需文件都已就绪
        
        TODO: 这里需要有digital的逻辑哦，而且这个判断以及缓冲区的初始化感觉不对 意思有1个也准备读？
        
        """
        # 必需：时间戳文件 + 至少一个数据文件
        # 这的判断逻辑不对，你得检测到指定的通道数 时间戳 以及所需要的样本类型数才能够准备好 这个还得考虑单intan的和双intan的,
        has_timestamp = self.file_processor.get_file_count_by_type('timestamp') > 0
        has_amp = self.file_processor.get_file_count_by_type('amp') >= 32
        has_digital = self.file_processor.get_file_count_by_type('digital_in') > 0
        
        if has_timestamp and has_amp and has_digital:
            # 初始化临时缓冲区
            amp_count = self.file_processor.get_file_count_by_type('amp')
            stim_count = self.file_processor.get_file_count_by_type('stim')
            digital_count = self.file_processor.get_file_count_by_type('digital_in')
            
            # TODO 这儿的格式初始化逻辑可能有问题 最终敲定就是这个格式？ 比如 temp_data_s 一旦返回带状态的字典就该崩溃了
            self.temp_data_t = np.empty(0, dtype=np.float32)
            self.temp_data_d = np.empty((amp_count, 0), dtype=np.float32)
            self.temp_data_s = np.empty((stim_count, 0), dtype=np.float32) if stim_count > 0 else None
            self.temp_data_di = np.empty((digital_count, 0), dtype=np.float32) if digital_count > 0 else None
            
            self.samples_per_100ms = int(self.sample_rate * 0.1)
            self.ready_to_load = True
            self._logger.info("Ready to load data")
            
    def _read_sample_rate_from_info(self, file_info):
        """从info文件读取采样率"""
        try:
            file_info.file_descriptor.read(8)  # 跳过前8字节
            # self.sample_rate = struct.unpack('f', file_info.file_descriptor.read(4))[0]
            self.sample_rate = 30000  # 暂时硬编码
            
            # 更新读取器的采样率
            self.reader_factory = DataReaderFactory(self.sample_rate)
        except Exception as e:
            self._logger.error("Failed to read sample rate: {}", e)
            
    def data_loading_task(self):
        """数据加载任务 - 使用新的组件"""
        while self.loading_running:
            try:
                if not self.ready_to_load:
                    time.sleep(0.01)
                    continue
                    
                # 计算可读取的样本数
                num_samples = self._calculate_available_samples()
                
                if num_samples >= self.min_samples_per_read:
                    # 读取各类型数据
                    t_start = time.perf_counter()  # 开始时间点
                    
                    new_data = self._read_all_data(num_samples)
                    
                    t_end = time.perf_counter()  # 结束时间点
                    elapsed_ms = (t_end - t_start) * 1000  # 转换为毫秒
                    self._logger.debug("all data read_delay {} ms", elapsed_ms) # 所有文件读取延迟计算
                    
                    # 处理数据块
                    t_start = time.perf_counter()  # 开始时间点
                    
                    self._process_data_blocks(new_data)
                    
                    t_end = time.perf_counter()  # 结束时间点
                    elapsed_ms = (t_end - t_start) * 1000  # 转换为毫秒
                    self._logger.debug("all data postprocess_delay {} ms", elapsed_ms) # 所有文件读取延迟计算
                    
                    self.stored_samples += num_samples
                    self._logger.debug("Loaded {} samples", num_samples)
                else:
                    time.sleep(0.01)
                    
            except Exception as e:
                self._logger.error("Error in data loading: {}", e)
                time.sleep(0.01)
                
    def _calculate_available_samples(self):
        """计算可用的样本数
        没有安全检查，有点痛，只考虑时间戳的样本数，其他的加上延迟太大
        """
        # 获取时间戳文件
        timestamp_files = self.file_processor.get_files_by_type('timestamp')
        if not timestamp_files:
            return 0   
        timestamp_size = os.path.getsize(timestamp_files[0].filename) // 4
        # available = min(timestamp_size, amp_min_size, digital_min_size)
        available = timestamp_size
        # available = min(timestamp_size, amp_min_size)
        
        return available - self.stored_samples
        
    def _read_all_data(self, num_samples):
        """使用新的读取器读取所有数据"""
        result = {}
        
        # 读取时间戳
        timestamp_files = self.file_processor.get_files_by_type('timestamp')
        if timestamp_files:
            reader = self.reader_factory.get_reader('timestamp')
            result['t'] = reader.read(timestamp_files[0].file_descriptor, num_samples)
            
        # 读取各类型数据
        for file_type in ['amp', 'stim', 'digital_in']:
            files = self.file_processor.get_files_by_type(file_type)
            if files:
                reader = self.reader_factory.get_reader(file_type)
                data_list = []
                for file_info in files:
                    data = reader.read(file_info.file_descriptor, num_samples)
                    data_list.append(data)
                    # 这里的 reset 其实没啥意思，怕外部调用，所以留着呢
                    # reader.reset()  # 重置每个文件的读取位置
                    
                result[file_type] = np.array(data_list)
                
        return result
        
    def _process_data_blocks(self, new_data):
        """处理数据块（简化版，保持原有逻辑）"""

        # 添加到临时缓冲区
        self.temp_data_t = np.concatenate((self.temp_data_t, new_data.get('t', [])))

        if 'amp' in new_data:
            self.temp_data_d = np.concatenate((self.temp_data_d, new_data['amp']), axis=1)

        if 'stim' in new_data:
            self.temp_data_s = np.concatenate((self.temp_data_s, new_data['stim']), axis=1)
        
        if 'digital_in' in new_data and self.temp_data_di is not None:
            self.temp_data_di = np.concatenate((self.temp_data_di, new_data['digital_in']), axis=1)   
        
        # 生成100ms块（保持原有逻辑）
        complete_blocks = self.temp_data_t.size // self.samples_per_100ms
        
        if complete_blocks > 0:
            end_idx = complete_blocks * self.samples_per_100ms
            
            for start in range(0, end_idx, self.samples_per_100ms):
                block_data = self._create_block_data(start, start + self.samples_per_100ms)
                self.circular_buffer.write(block_data)
                
            # 更新剩余数据 是不是得成功后才需要更新？
            if self.temp_data_t is not None:
                self.temp_data_t = self.temp_data_t[end_idx:]
            if self.temp_data_d is not None:
                self.temp_data_d = self.temp_data_d[:, end_idx:]
            if self.temp_data_s is not None:
                self.temp_data_s = self.temp_data_s[:, end_idx:]
            if self.temp_data_di is not None:
                self.temp_data_di = self.temp_data_di[:, end_idx:]
                
    def _create_block_data(self, start, end):
        """创建数据块（保持原有格式）"""
        block_data = {
            't': self.temp_data_t[start:end],
            'd': {},
            's': {},
            'di': {}
        }
        
        # 添加amp数据
        if self.temp_data_d is not None:
            for i in range(self.temp_data_d.shape[0]):
                block_data['d']['d_{}'.format(i+1)] = self.temp_data_d[i, start:end]
            
        # 添加stim数据
        if self.temp_data_s is not None:
            for i in range(self.temp_data_s.shape[0]):
                block_data['s']['s_{}'.format(i+1)] = self.temp_data_s[i, start:end]
            
        # 添加digital数据
        if self.temp_data_di is not None:
            for i in range(self.temp_data_di.shape[0]):
                block_data['di']['di_{}'.format(i+1)] = self.temp_data_di[i, start:end]
                
        return block_data
        
    # 保留原有的其他方法...
    def start_data_loading_thread(self):
        """启动数据加载线程"""
        self.loading_running = True
        self.data_loading_thread = Thread(target=self.data_loading_task)
        self.data_loading_thread.start()
        
    def stop_data_loading_thread(self):
        """停止数据加载线程"""
        self.loading_running = False
        if self.data_loading_thread:
            self.data_loading_thread.join()
            self.data_loading_thread = None
            
    def read_data(self, timespan_ms):
        """
        根据指定的时间跨度（毫秒）从环形缓冲区中读取拼接后的二维数组和时间戳。

        参数:
        - timespan_ms: 整数，表示时间跨度，以毫秒为单位，应为100ms的整数倍。

        返回值:
        - NumPy多维数组，形状为 (通道数, 样本数)，其中样本数为 timespan_ms 转换后的样本数。
        - NumPy多维数组，形状为 (刺激通道数, 样本数)，表示对应的刺激数据。
        - NumPy数组，长度与样本数一致，表示时间戳。
        - NumPy多维数组，形状为 (数字通道数, 样本数)，表示数字输入数据，如果没有则返回None。
        """
        
        if not self.ready_to_load or not self.sample_rate:
            self._logger.warning("Data not ready for reading")
            return None, None, None, None
        
        # self._logger.debug("Start read_data time: {}", time.time())
        samples_needed = int(self.sample_rate * (timespan_ms / 1000.0))
        blocks_needed = samples_needed // self.samples_per_100ms

        # 获取各类型文件的数量（通道数）
        amp_count = self.file_processor.get_file_count_by_type('amp')
        stim_count = self.file_processor.get_file_count_by_type('stim')
        digital_count = self.file_processor.get_file_count_by_type('digital_in')
        
        if amp_count == 0:
            self._logger.warning("No amp files available")
            return None, None, None, None

        # 初始化收集数据的容器
        collected_t = np.empty(0, dtype=np.float32)
        collected_d = np.empty((amp_count, 0), dtype=np.float32)
        collected_s = np.empty((stim_count, 0), dtype=np.float32) if stim_count > 0 else None
        collected_di = np.empty((digital_count, 0), dtype=np.float32) if digital_count > 0 else None

        blocks_collected = 0

        while blocks_collected < blocks_needed:
            if self.circular_buffer.size > 0:  # 确保缓冲区内有数据
                # 从环形缓冲区读取最新的数据块
                channel_data_dict = self.circular_buffer.read_optimized()
                
                if not channel_data_dict:
                    self._logger.debug("No data block available from buffer")
                    break

                # 提取并拼接时间戳和数据
                collected_t = np.concatenate((collected_t, channel_data_dict['t']))
                
                # 拼接amp数据
                if channel_data_dict['d']:
                    new_data_d = np.array([channel_data_dict['d']['d_{}'.format(i + 1)] 
                                        for i in range(amp_count)])
                    collected_d = np.concatenate((collected_d, new_data_d), axis=1)
                
                # 拼接stim数据
                if collected_s is not None and channel_data_dict['s']:
                    new_data_s = np.array([channel_data_dict['s']['s_{}'.format(i + 1)] 
                                        for i in range(stim_count)])
                    collected_s = np.concatenate((collected_s, new_data_s), axis=1)
                
                # 拼接digital数据
                if collected_di is not None and channel_data_dict['di']:
                    new_data_di = np.array([channel_data_dict['di']['di_{}'.format(i + 1)] 
                                        for i in range(digital_count)])
                    collected_di = np.concatenate((collected_di, new_data_di), axis=1)
                    
                blocks_collected += 1
            else:
                self._logger.debug("No new data available in the buffer")
                break

        # self._logger.debug("End read_data time: {}", time.time())
        
        # 检查数据是否足够 TODO 数字输入的状态没检查哦
        data_sufficient = (collected_t.size >= samples_needed and 
                        collected_d.shape[1] >= samples_needed)
        
        if collected_s is not None:
            data_sufficient = data_sufficient and collected_s.shape[1] >= samples_needed
            
        if collected_di is not None:
            data_sufficient = data_sufficient and collected_di.shape[1] >= samples_needed
        
        if data_sufficient:
            # 截取到精确的样本数
            final_t = collected_t[:samples_needed]
            final_d = collected_d[:, :samples_needed]
            final_s = collected_s[:, :samples_needed] if collected_s is not None else None
            final_di = collected_di[:, :samples_needed] if collected_di is not None else None
            
            self._logger.debug("Successfully read {} samples from {} blocks for {}ms timespan", 
                            samples_needed, blocks_collected, timespan_ms)
            
            return final_d, final_s, final_t, final_di
        else:
            self._logger.debug("Insufficient data available: need {}, got t={}, d={}", 
                            samples_needed, collected_t.size, collected_d.shape[1])
            return None, None, None, None
        
        
if __name__ == "__main__":

    directory_to_monitor1 = "F:\\Intan"  # 要监控的目录
    # directory_to_monitor2 = "E:/TCP/Data/2"  # 要监控的目录
    reader = RealTimeDataReader()
    reader.set_monitoring_directory(directory_to_monitor1)
    print("开始读取数据")
    while True:
        t_start = time.perf_counter()  # 开始时间点
        once_data, sti_time, timestamp, digital = reader.read_data(100)    # 输入参数表示100ms
        t_end = time.perf_counter()  # 结束时间点
        elapsed_ms = (t_end - t_start) * 1000  # 转换为毫秒
        reader._logger.debug("extern data read_delay {} ms", elapsed_ms) # 所有文件读取延迟计算
        time.sleep(0.5)
    
    
    