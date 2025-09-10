from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from log_manager import LogManager

import numpy as np
import os
import time
import matplotlib.pyplot as plt
import struct  # 用于处理二进制数据

import datetime

from threading import Thread, Event
from queue import Queue
import sys

from threading import Thread, Event
from queue import Queue, Empty

class FileMonitorHandler(FileSystemEventHandler):
    # 文件监控类
    def __init__(self, reader):
        self.reader = reader
        self.latestfile = ""

    def on_created(self, event):
        if event.is_directory:
            # 新目录创建，打印路径，但不在这里处理
            self.logger.debug(f"New directory created: {event.src_path}")
        if any(event.src_path.endswith(ext) for ext in ['.dat', '.rhs']):
            # if self.latestfile != event.src_path:
            self.reader.handle_new_file(event.src_path)
            # self.latestfile = event.src_path


class CircularBuffer:
    def __init__(self, capacity):
        """
        初始化环形缓冲区。

        参数:
            capacity (int): 缓冲区的容量。
        """
        self.capacity = capacity
        self.buffer = [None] * capacity
        self.head = 0
        self.size = 0
        self.read_index = 0

        # ----------------------设置日志输出-----------------------
        self.log_manager = log_manager  # 使用LogManager实例
        self.logger = log_manager.get_logger()  # 获取self.logger实例

    def setup_logging(self, log_file_path):
        """设置log存储"""
        log_folder = os.path.dirname(log_file_path)
        os.makedirs(log_folder, exist_ok=True)
        self.logger.add(log_file_path, rotation="100 MB")  # Rotates the log file when it reaches 10MB

    def write(self, data):
        """
        将数据写入缓冲区。

        参数:
            data (any): 要写入的数据。
        """
        index = (self.head + self.size) % self.capacity
        self.buffer[index] = data
        if self.size < self.capacity:
            self.size += 1
        else:
            self.head = (self.head + 1) % self.capacity

    def read(self, index=0):
        """
        读取缓冲区中指定索引处的数据。

        参数:
            index (int): 从最老的数据算起的索引（0是最老的，size-1是最新的）。

        返回:
            (any): 位于指定索引处的数据。
        """
        if index >= self.size:
            return None  # 索引超出当前存储的数据量
        actual_index = (self.head + index) % self.capacity
        return self.buffer[actual_index]

    def read_next(self):
        """
        读取下一个数据项，并自动更新读取索引。

        返回:
            (any): 从缓冲区中读取的下一个数据项，如果没有更多数据则返回 None。
        """
        if self.size == 0 or self.read_index >= self.size:
            return None
        actual_index = (self.head + self.read_index) % self.capacity
        data = self.buffer[actual_index]
        self.read_index = (self.read_index + 1) % self.capacity  # 更新读取索引
        return data

    def read_newest(self):
        """
        获取缓冲区中最新的数据。

        返回:
            (any): 缓冲区中最新的数据。
        """
        if self.size == 0:
            return None
        latest_index = (self.head + self.size - 1) % self.capacity
        return self.buffer[latest_index]

    def read_optimized(self):
        """
        尝试读取最新的数据，如果数据落后太多，则跳跃到接近最新的位置。
        """
        if self.size == 0 or self.read_index >= self.size:
            return None

        # 计算理论上的最新索引
        latest_index = (self.head + self.size - 1) % self.capacity

        # 计算当前读取索引与最新索引的差距
        gap = (latest_index - self.read_index + self.capacity) % self.capacity

        # 如果差距太大，说明读取落后太多，需要跳跃
        base_gap = 5
        if gap > base_gap:  # 例如，如果落后超过500ms，则更新指针
            self.logger.debug("f---------------------------------------------已积累50ms延迟，需要进行修正---------------------------------------")
            # self.read_index = (latest_index - base_gap + self.capacity) % self.capacity  # 跳跃到较新的位置
            self.read_index = latest_index  # 跳到最新数据位置

        # 读取当前的数据
        data = self.buffer[self.read_index]
        self.read_index = (self.read_index + 1) % self.capacity  # 更新读取索引
        return data

    def clear(self):
        """清除缓冲区中的所有数据。"""
        self.head = 0
        self.size = 0
        self.buffer = [None] * self.capacity

    def read_all(self):
        """读取缓冲区中的所有数据，从最老到最新。

        返回:
            list: 包含缓冲区中所有数据的列表。
        """
        items = []
        for i in range(self.size):
            items.append(self.buffer[(self.head + i) % self.capacity])
        return items



class RealTimeDataReader:
    # 数据实时读取类
    def __init__(self, log_manager):

        # ----------------------目录监控相关-----------------------
        # 在监控的父级目录.
        self.directory_to_monitor = None
        # 监控目录中的子目录，子目录中包含对应的通道数据文件
        self.latest_subdirectory = None
        # 用于指示是否监控记录完毕，可以开始加载数据
        self.ready_to_load = False

        # ----------------------磁盘文件索引相关-----------------------
        # 文件名列表.
        self.filenames = []
        self.timestamp_filename = None
        # 采样率将从 info 文件中读取
        self.sample_rate = 30000
        # 文件描述符列表.
        self.d_fids = []
        # 时间戳数据文件描述符
        self.t_fid = None
        # 刺激数据文件描述符
        self.stim_fids = []
        # 刺激数据转化所需步长，目前不明确值为多少
        self.stim_step_size = 10
        # 通道名列表
        self.stim_filenames = []
        # 刺激通道名列表
        self.amp_filenames = []
        # ----------------------缓冲池设置相关-----------------------
        self.data_queue = Queue()
        self.min_samples_per_read = 1000
        # 已追踪样本数
        self.stored_samples = 0
        # 放大器数据数据缩放
        self.d_scale = 0.195
        # -----------------------实时环形缓冲池-----------------------
        self.circular_buffer = CircularBuffer(20)  # 设定环形缓冲区的大小

        # 临时存储数据缓冲区
        self.samples_per_100ms = None  # 在 `data_loading_task` 中进行初始化
        self.temp_data_t = None
        self.temp_data_d = None

        # 缓冲池大小监控线程相关
        self.max_queue_size = 20   # 10000s的数据最大
        self.safe_queue_size = 10  # 8000s时进行数据清空 13min的数据
        self.monitor_interval = 1  # 5分钟间隔

        # 若启用定时器，不使用多线程
        self.monitor_thread_queue = None
        self.monitor_running_queue = False

        # ----------------------绘图测试相关-----------------------
        self.plotting_thread = None
        self.spacing = 1000 # 绘图测试用 通道垂直间距
        # 初始化一秒数据窗口
        self.one_second_data_t = np.empty(0, dtype=np.float32)
        self.one_second_data_d = np.empty((64, 0), dtype=np.float32)  # 假设有64个数据通道
        self.one_second_data_s = np.empty((6, 0), dtype=np.float32)   # 假设有6个刺激通道
        
        # ----------------------设置日志输出-----------------------
        self.log_manager = log_manager  # 使用LogManager实例
        self.logger = log_manager.get_logger()  # 获取self.logger实例

        # ----------------------多线程相关-----------------------
        # 监控线程
        self.observer = None
        # 用于指示监控是否正在运行
        self.monitor_running = False
        # 数据加载线程
        self.data_loading_thread = None
        # 用于指示数据加载是否正在运行
        self.loading_running = False

        # 线程启动，启动位置需要斟酌，这里加载线程直接构造启动.
        # 同样，何时终止以及线程间的信号通信等后续有需求时再作开发.
        self.start_data_loading_thread()
        # 监控线程的启动由 set_monitoring_directory 启动
        # self.start_monitoring()

        # 启动队列监控线程，若启用定时器，不使用多线程
        # self.start_monitoring_Queue()
    
    def setup_logging(self, log_file_path):
        """设置log存储"""
        log_folder = os.path.dirname(log_file_path)
        os.makedirs(log_folder, exist_ok=True)
        self.logger.add(log_file_path, rotation="100 MB")  # Rotates the log file when it reaches 10MB

    def start_monitoring_Queue(self):
        """启动监控线程"""
        self.monitor_running_queue = True
        self.monitor_thread = Thread(target=self.queue_monitoring_task)
        self.monitor_thread.start()

    def stop_monitoring_Queue(self):
        """停止监控线程"""
        self.monitor_running_queue = False
        if self.monitor_thread:
            self.monitor_thread.join()
            self.monitor_thread = None

    def start_monitoring(self):
        """初始化并启动文件监视器。"""

        if not self.monitor_running:
            self.observer = Observer()
            event_handler = FileMonitorHandler(self)
            self.observer.schedule(event_handler, self.directory_to_monitor, recursive=True)
            self.observer.start()
            self.monitor_running = True
            self.logger.debug("Started monitoring directory:", self.directory_to_monitor)

    def stop_monitoring(self):
        """停止文件监视器并等待其完全停止。"""

        if self.monitor_running:
            self.observer.stop()  # 告诉observer停止监控
            self.observer.join()  # 等待监控线程完全停止
            self.observer.unschedule_all()  # 清除所有监控任务
            self.monitor_running = False
            self.logger.debug("Stopped monitoring directory:", self.directory_to_monitor)

    def start_data_loading_thread(self):
        """启动数据加载线程"""

        # 直接设置self.loading_running为True，表示有加载任务需要执行
        self.loading_running = True
        # 每次都创建新的线程实例来执行数据加载任务
        self.data_loading_thread = Thread(target=self.data_loading_task)
        # 启动新创建的线程
        self.data_loading_thread.start()

    def stop_data_loading_thread(self):
        """停止数据加载线程并进行清理"""

        # 设置self.loading_running为False，通知数据加载任务停止执行
        self.loading_running = False
        if self.data_loading_thread:
            # 等待当前正在执行的线程结束
            self.data_loading_thread.join()
            # 将self.data_loading_thread设置为None，这样下次调用start_data_loading_thread时，
            # 将会创建一个新的线程实例。
            self.data_loading_thread = None

    def queue_monitoring_task(self):
        """
        队列监控任务的主循环。

        此方法作为一个后台线程运行，定期检查数据队列的大小，并根据最大阈值执行清理或警告操作。

        参数:
        无

        返回值:
        无
        """
        while self.monitor_running_queue:
            try:
                queue_size = self.data_queue.qsize()
                self.logger.debug(f"Current queue size: {queue_size}")

                if queue_size > self.max_queue_size:
                    items_removed = 0
                    self.logger.debug("Queue exceeds max size, discarding old data")

                    while self.data_queue.qsize() > self.safe_queue_size:
                        try:
                            self.data_queue.get_nowait()  # 弹出旧数据
                            items_removed += 1
                        except Empty:
                            break

                    self.logger.debug(f"Removed {items_removed} items from the queue to reach the safe size.")

                time.sleep(self.monitor_interval)
            except Exception as e:
                self.logger.debug(f"Error in queue monitoring task: {e}")
                time.sleep(0.1)

    def data_loading_task(self):
        """
        数据加载任务的主循环。

        此方法作为一个后台线程运行，负责周期性地检查是否有新的数据文件就绪
        并从这些文件中读取数据。读取的数据会被缩放并以字典的形式放入队列中，
        供其他部分的代码进一步处理。

        注意该接口读取逻辑为最大化读取，重组切割后存储，使得每个元素为 100 ms 的数据块.

        方法执行流程：
        1. 检查是否所有必需的文件描述符都已就绪。如果没有，等待0.1秒后再次检查。
        2. 计算当前可用的样本数量，基于时间戳文件和数据文件的大小。
        3. 如果可用的样本数量大于设定的缓冲大小，则从文件中读取这些样本。
        4. 从时间戳文件中读取时间戳数据，并从每个数据文件中读取样本数据。
        5. 将读取的数据缩放重组后存入字典，然后将字典放入队列中。
        6. 更新已存储的样本数量，并打印当前队列的状态信息。

        参数:
        无

        返回值:
        无

        注意:
        - 在数据文件格式或采样率发生变化时，需要相应地调整读取和缩放逻辑。
        """

        # self.samples_per_100ms = int(self.sample_rate * 0.1)  # 100ms对应的样本数
        self.samples_per_100ms = int(self.sample_rate * 0.1)  # 100ms对应的样本数

        # self.temp_data_t = np.empty(0, dtype=np.float32)
        # # 后期准备改，该维度应该与文件描述符列表匹配，在监控到数据产生后进行数据加载
        # self.temp_data_d = np.empty((64, 0), dtype=np.float32)
        # # 这里的6需要与监控到数据后的维度一致
        # self.temp_data_s = np.empty((2, 0), dtype=np.float32)  # 初始化存储刺激数据的临时缓冲区

        while self.loading_running:
            try:
                if not self.ready_to_load:
                    time.sleep(0.01)
                    continue

                self.logger.debug(f"Start data loading time: {time.time()}.")

                available_samples_t = os.path.getsize(self.timestamp_filename) // 4
                available_samples_d = min(os.path.getsize(f) // 2 for f in self.filenames) if self.filenames else 0
                available_samples = min(available_samples_t, available_samples_d)
                num_samples = available_samples - self.stored_samples

                if num_samples >= self.min_samples_per_read:
                    # 读取新的数据
                    new_data_t = self._read_timestamp(num_samples)
                    new_data_d = self._read_data(num_samples)
                    new_data_s = self._read_stimulation_data(num_samples)["Stimdata"]

                    # 将新数据添加到临时缓冲区
                    self.temp_data_t = np.concatenate((self.temp_data_t, new_data_t))
                    self.temp_data_d = np.concatenate((self.temp_data_d, new_data_d), axis=1)
                    self.temp_data_s = np.concatenate((self.temp_data_s, new_data_s), axis=1)

                    # 计算可以生成多少个完整的100ms块
                    complete_blocks = self.temp_data_t.size // self.samples_per_100ms

                    if complete_blocks > 0:
                        end_idx = complete_blocks * self.samples_per_100ms

                        for start in range(0, end_idx, self.samples_per_100ms):
                            block_data_t = self.temp_data_t[start:start + self.samples_per_100ms]
                            block_data_d = self.temp_data_d[:, start:start + self.samples_per_100ms]
                            block_data_s = self.temp_data_s[:, start:start + self.samples_per_100ms]  # 获取刺激数据块
                            channel_data_dict = {'t': block_data_t, 'd': {}, 's': {}}  # 添加刺激数据到字典

                            for i, fid in enumerate(self.d_fids):
                                channel_key = f'd_{i + 1}'
                                channel_data_dict['d'][channel_key] = block_data_d[i, :]

                            for j, s_fid in enumerate(self.stim_fids):
                                stim_key = f's_{j + 1}'
                                channel_data_dict['s'][stim_key] = block_data_s[j, :]

                            self.data_queue.put(('data', channel_data_dict))
                    
                        self.logger.debug(f"End data loading time: {time.time()}.")

                        # 更新剩余数据
                        self.temp_data_t = self.temp_data_t[end_idx:]
                        self.temp_data_d = self.temp_data_d[:, end_idx:]
                        self.temp_data_s = self.temp_data_s[:, end_idx:]  # 更新剩余刺激数据

                    self.stored_samples += num_samples
                    self.logger.debug(f"current_storing_samples: {num_samples}")
                    self.logger.debug(f"stored_samples: {self.stored_samples}")
                    self.logger.debug(f"Size of the queue: {self.data_queue.qsize()}\n")
                else:
                    time.sleep(0.01)
            except Exception as e:
                self.logger.debug(f"Error loading data: {e}")
                time.sleep(0.01)

    def data_loading_task_inCircular(self):
        """
        数据加载任务的主循环。

        此方法作为一个后台线程运行，负责周期性地检查是否有新的数据文件就绪
        并从这些文件中读取数据。读取的数据会被缩放并以字典的形式放入环形缓冲池中，
        供其他部分的代码进一步处理。

        注意该接口读取逻辑为最大化读取，重组切割后存储，使得每个元素为 100 ms 的数据块.

        方法执行流程：
        1. 检查是否所有必需的文件描述符都已就绪。如果没有，等待0.1秒后再次检查。
        2. 计算当前可用的样本数量，基于时间戳文件和数据文件的大小。
        3. 如果可用的样本数量大于设定的缓冲大小，则从文件中读取这些样本。
        4. 从时间戳文件中读取时间戳数据，并从每个数据文件中读取样本数据。
        5. 将读取的数据缩放重组后存入字典，然后将字典放入队列中。
        6. 更新已存储的样本数量，并打印当前队列的状态信息。

        参数:
        无

        返回值:
        无

        注意:
        - 在数据文件格式或采样率发生变化时，需要相应地调整读取和缩放逻辑。
        """

        # self.samples_per_100ms = int(self.sample_rate * 0.1)  # 100ms对应的样本数
        self.samples_per_100ms = int(self.sample_rate * 0.1)  # 100ms对应的样本数

        # self.temp_data_t = np.empty(0, dtype=np.float32)
        # # 后期准备改，该维度应该与文件描述符列表匹配，在监控到数据产生后进行数据加载
        # self.temp_data_d = np.empty((64, 0), dtype=np.float32)
        # # 这里的6需要与监控到数据后的维度一致
        # self.temp_data_s = np.empty((2, 0), dtype=np.float32)  # 初始化存储刺激数据的临时缓冲区

        while self.loading_running:
            try:
                if not self.ready_to_load:
                    time.sleep(0.01)
                    continue

                self.logger.debug(f"Start data loading time: {time.time()}.")

                available_samples_t = os.path.getsize(self.timestamp_filename) // 4
                available_samples_d = min(os.path.getsize(f) // 2 for f in self.filenames) if self.filenames else 0
                available_samples = min(available_samples_t, available_samples_d)

                num_samples = self.calculate_available_samples(available_samples, self.stored_samples)

                if num_samples >= self.min_samples_per_read:
                    # 读取新的数据
                    new_data_t = self._read_timestamp(num_samples)
                    new_data_d = self._read_data(num_samples)
                    new_data_s = self._read_stimulation_data(num_samples)["Stimdata"]

                    # 将新数据添加到临时缓冲区
                    self.temp_data_t = np.concatenate((self.temp_data_t, new_data_t))
                    self.temp_data_d = np.concatenate((self.temp_data_d, new_data_d), axis=1)
                    self.temp_data_s = np.concatenate((self.temp_data_s, new_data_s), axis=1)

                    # 计算可以生成多少个完整的100ms块
                    complete_blocks = self.temp_data_t.size // self.samples_per_100ms

                    if complete_blocks > 0:
                        end_idx = complete_blocks * self.samples_per_100ms

                        for start in range(0, end_idx, self.samples_per_100ms):
                            block_data_t = self.temp_data_t[start:start + self.samples_per_100ms]
                            block_data_d = self.temp_data_d[:, start:start + self.samples_per_100ms]
                            block_data_s = self.temp_data_s[:, start:start + self.samples_per_100ms]  # 获取刺激数据块
                            channel_data_dict = {'t': block_data_t, 'd': {}, 's': {}}  # 添加刺激数据到字典

                            for i, fid in enumerate(self.d_fids):
                                channel_key = f'd_{i + 1}'
                                channel_data_dict['d'][channel_key] = block_data_d[i, :]

                            for j, s_fid in enumerate(self.stim_fids):
                                stim_key = f's_{j + 1}'
                                channel_data_dict['s'][stim_key] = block_data_s[j, :]

                            self.circular_buffer.write(channel_data_dict)

                        self.logger.debug(f"End data loading time: {time.time()}.")

                        # 更新剩余数据
                        self.temp_data_t = self.temp_data_t[end_idx:]
                        self.temp_data_d = self.temp_data_d[:, end_idx:]
                        self.temp_data_s = self.temp_data_s[:, end_idx:]  # 更新剩余刺激数据

                    self.stored_samples += num_samples
                    self.logger.debug(f"current_storing_samples: {num_samples}")
                    self.logger.debug(f"stored_samples: {self.stored_samples}")
                    self.logger.debug(f"Size of the queue: {self.circular_buffer.size}\n")
                else:
                    time.sleep(0.01)
            except Exception as e:
                self.logger.debug(f"Error loading data: {e}")
                time.sleep(0.01)

    def handle_new_subdirectory(self, filename):
        """
        处理新子目录的创建事件。

        当监控到的文件所在目录与最新追踪的子目录不同时，将进行目录更新操作。
        这包括重置文件名列表、关闭并重置文件描述符、清空数据缓冲队列，并重置已追踪样本数。

        参数:
        - filename: 触发目录更新事件的文件的完整路径。根据这个路径计算出的目录，
                    如果与当前追踪的最新子目录不同，则触发更新逻辑。

        返回值:
        无
        """

        new_subdirectory = os.path.dirname(filename)

        # 如果最新子目录为空，或者新文件所在目录与最新子目录不同，更新目录
        if self.latest_subdirectory is None or self.latest_subdirectory != new_subdirectory:
            self.logger.debug(f"Switching to new directory: {new_subdirectory}")
            self.latest_subdirectory = new_subdirectory

            # 重置文件名列表和其他相关状态
            self.filenames = []
            self.timestamp_filename = None
            # 描述符列表清空
            if self.t_fid:
                self.t_fid.close()
                self.t_fid = None
            if self.d_fids:
                for fid in self.d_fids:
                    fid.close()
                self.d_fids = []
            if self.stim_fids:
                for fid in self.stim_fids:
                    fid.close()
                self.stim_fids = []
            # 缓冲池清空
            # 注意存在丢失问题，所以何时清空缓冲池中数据后续需要考虑.
            with self.data_queue.mutex:
                self.data_queue.queue.clear()
            # 更新追踪样本数
            self.stored_samples = 0

    def handle_new_file(self, filename):
        """
        处理监控到的新文件。

        当文件系统监控事件触发并检测到新文件时，此方法负责处理新文件。这包括：
        - 检查并更新当前监控的子目录。
        - 根据文件类型（时间戳文件、信息文件或数据文件），执行对应的数据加载准备操作。
        - 如果所有必需的文件（时间戳文件、数据文件）已就绪，设置准备加载数据的标志。

        注意：该方法假定文件按照特定的扩展名（.dat, .rhs）进行区分。

        参数:
        - filepath: 新创建或修改的文件的完整路径。

        返回值:
        无
        """

        # 新文件产生时，不允许数据继续加载，因为缓冲区维度会变
        self.ready_to_load = False

        # 检查是否为新目录并作处理.
        self.handle_new_subdirectory(filename)

        # 获取文件的基础名称（不带路径部分）
        basename = os.path.basename(filename)

        # 检测并处理新文件
        if filename not in self.filenames:
            if 'time.dat' in filename:
                self.timestamp_filename = filename
                self.t_fid = self.open_file(filename)
            elif 'info.rhs' in filename:
                self.read_sample_rate_from_info_file(filename)
            elif basename.startswith('stim') and basename.endswith('.dat'):
                self.logger.debug(f"Added stimulation file: {filename}")
                self.filenames.append(filename)
                self.stim_fids.append(self.open_file(filename))
                self.stim_filenames.append(basename)
            elif basename.startswith('amp') and basename.endswith('.dat'):
                self.filenames.append(filename)
                self.logger.debug(f"Added Amp file: {filename}")
                self.d_fids.append(self.open_file(filename))
                self.amp_filenames.append(basename)

        # 检查是否所有必需的文件都已添加
        if self.t_fid and self.d_fids and self.stim_fids and self.sample_rate:
            # 动态初始化缓冲区的维度
            self.temp_data_t = np.empty(0, dtype=np.float32)
            self.temp_data_d = np.empty((len(self.d_fids), 0), dtype=np.float32)
            self.temp_data_s = np.empty((len(self.stim_fids), 0), dtype=np.float32)
            self.ready_to_load = True

    def set_monitoring_directory(self, new_directory):
        """
        更新要监控的目录路径，并重启目录监控。

        此方法负责更新数据读取器的监控目录。在更新目录之前，它会停止当前的监控活动
        和数据加载进程，清空所有相关状态和队列，然后启动新的监控活动。这确保了数据
        读取器始终监控正确的目录，并处理最新的文件。

        参数:
        - new_directory: 字符串，指定新的监控目录的路径。

        返回值:
        无

        注意:
        - 在更换监控目录时，正在队列中等待处理的数据将会被清空，可能会导致数据丢失。
          因此需要格外强调何时进行缓冲池的清空这个问题，后续再考虑.
        - 在调用此方法时，请确保新目录是有效的，并且应用程序有权限访问该目录。
        """

        self.logger.debug(f"Updating monitoring directory to: {new_directory}")

        # 使用self.monitor_running来判断监控线程的状态
        if self.monitor_running:
            # 调用stop_monitoring方法来停止当前的监控活动
            self.stop_monitoring()
            self.logger.debug("Directory monitoring stopped.")

        # 停止数据加载线程的加载，防止加载 None 对象
        self.ready_to_load = False

        # 更新监控的目录
        self.directory_to_monitor = new_directory

        # 重置相关状态
        self.latest_subdirectory = None  # 确保更新最新的子目录引用
        self.filenames = []
        self.timestamp_filename = None
        # 关闭并重置所有打开的文件句柄
        if self.t_fid:
            self.t_fid.close()
            self.t_fid = None
        if self.d_fids:
            for fid in self.d_fids:
                fid.close()
            self.d_fids = []
        if self.stim_fids:
            for fid in self.stim_fids:
                fid.close()
            self.stim_fids = []
        # 清空数据队列
        # 注意存在丢失问题，所以何时清空缓冲池中数据后续需要考虑.
        with self.data_queue.mutex:
            self.data_queue.queue.clear()
        # 更新追踪样本数
        self.stored_samples = 0

        # 重新启动监控到新的目录
        self.start_monitoring()
        self.logger.debug("Monitoring restarted for the new directory.")

    def read_sample_rate_from_info_file(self, info_filename):
        """
        从指定的信息文件中读取并设置采样率。

        此方法打开一个包含采样率信息的二进制文件，跳过文件开头的8个字节，
        然后读取接下来的4个字节作为浮点数，这4个字节代表了数据的采样率。
        读取到的采样率将被存储在self.sample_rate中。

        参数:
        - info_filename: 字符串，包含了采样率信息的文件的路径。

        返回值:
        无
        """

        try:
            with open(info_filename, 'rb') as info_file:
                # 跳过前 8 个字节
                info_file.read(8)
                # 读取采样率 (float32)
                self.sample_rate = struct.unpack('f', info_file.read(4))[0]
        except IOError:
            self.logger.debug(f"Failed to read {info_filename}. Is it in this directory?")

    def open_file(self, filename):
        """
        尝试以二进制读取模式打开指定的文件。

        此方法尝试打开给定的文件名对应的文件，并返回文件的描述符。
        如果文件成功打开，文件描述符将被返回；如果打开文件失败（例如，
        文件不存在或没有读取权限），将打印错误信息并返回None。

        参数:
        - filename: 字符串，要打开的文件的路径。

        返回值:
        - 成功打开文件时，返回文件的描述符。
        - 打开文件失败时，返回None。
        """

        try:
            fid = open(filename, 'rb')
            return fid
        except IOError:
            self.logger.debug(f"Failed to read {filename}. Is it in this directory?")
            return None

    def open_files(self, filenames):
        """
        批量打开给定列表中的文件，并返回打开的文件描述符列表。

        此方法遍历一个包含文件名的列表，尝试打开每个文件。对于成功打开的文件，
        其文件描述符将被添加到返回的列表中。如果某个文件无法打开，则跳过该文件，
        不会影响其他文件的打开过程。这个方法使用了`open_file`方法来单独打开每个文件，
        并处理打开文件过程中可能出现的异常。

        参数:
        - filenames: 一个字符串列表，包含需要打开的文件的路径。

        返回值:
        - 一个包含成功打开的文件的文件描述符的列表。如果所有文件都无法打开，将返回一个空列表。
        """

        fids = []
        for filename in filenames:
            fid = self.open_file(filename)
            if fid is not None:
                fids.append(fid)
        return fids

    def _read_timestamp(self, num_samples):
        """
        从时间戳文件中读取指定数量的样本，并按照采样率调整时间戳。

        此方法从当前打开的时间戳文件（self.t_fid）中读取指定数量的样本。
        每个样本都是一个32位整数，代表从开始记录以来的采样点数。读取的样本将
        被转换为按照采样率调整后的时间戳，即每个样本对应的实际时间（秒）。

        参数:
        - num_samples: 整数，指定要从文件中读取的样本数量。

        返回值:
        - 一个NumPy数组，包含根据采样率调整后的时间戳。如果采样率未知，则返回原始样本值。

        注意:
        - 在调用此方法之前，请确保时间戳文件已通过`open_file`方法成功打开，并且`self.t_fid`不为None。
        - 同时，确保已正确设置`self.sample_rate`以反映数据的采样率。
        - 此方法假设时间戳文件中的数据格式为32位整数（np.int32）。
        """

        # 从time.dat读取时间戳数据
        if self.t_fid.seekable():
            self.t_fid.seek(self.stored_samples * 4)  # 定位到上次读取的位置

        self.logger.debug(f"Current position in timestamp file (before read): {self.t_fid.tell()}")
        t_data = np.fromfile(self.t_fid, dtype=np.int32, count=num_samples)
        self.logger.debug(f"Current position in timestamp file (after read): {self.t_fid.tell()}")

        return t_data / self.sample_rate

    def _read_data(self, num_samples):
        '''
        从所有打开的数据文件中读取指定数量的样本，并应用放大器缩放。
        此方法遍历所有已打开的数据文件描述符（self.d_fids 中的每个项），从每个文件中读取
        指定数量的样本。读取的样本是16位整数格式，代表放大器采集到的原始数据。为了将这些
        原始数据转换为实际的物理量度（例如，电压），每个样本值将乘以一个缩放因子（Intan 提
        供的样例为 0.195）。
        参数:
        - num_samples: 整数，指定要从每个文件中读取的样本数量。
        返回值:
        - 一个NumPy数组，其中包含从每个数据文件中读取的样本值，已按照放大器缩放调整。
        数组的形状为(通道数, num_samples)，每一行对应一个通道的数据。
        '''
        data = []
        for i, fid in enumerate(self.d_fids):
            if fid.seekable():
                fid.seek(self.stored_samples * 2)
            self.logger.debug(f"Current position in data file {i + 1} (before read): {fid.tell()}")
            d_data = np.fromfile(fid, dtype=np.int16, count=num_samples)
            data.append(d_data * self.d_scale)
            self.logger.debug(f"Current position in data file {i + 1} (after read): {fid.tell()}")
        return np.array(data)

    def _read_stimulation_data(self, num_samples):
        """
        以增量形式读取并解析刺激文件数据，Stimdata 数据非0，则代表施加了刺激。

        参数:
        - num_samples: 整数，指定要读取的样本数量。

        返回值:
        - 一个字典，包含电流值和状态位信息。
          - 'Stimdata': 列表，包含所有刺激文件中的电流数据。
          - 'compliance_limit': 列表，包含合规限制状态数据。
          - 'charge_recovery': 列表，包含充电恢复状态数据。
          - 'amplifier_settle': 列表，包含放大器稳定状态数据。
        """

        stim_data_combined = {
            'Stimdata': [],
            'compliance_limit': [],
            'charge_recovery': [],
            'amplifier_settle': []
        }

        for i, fid in enumerate(self.stim_fids):
            if fid.seekable():
                fid.seek(self.stored_samples * 2)  # 定位到上次读取的位置
            self.logger.debug(f"Current position in stim file {i + 1} (before read): {fid.tell()}")

            data = np.fromfile(fid, dtype=np.uint16, count=num_samples)
            self.logger.debug(f"Current position in stim file {i + 1} (after read): {fid.tell()}")

            current_magnitude = np.bitwise_and(data, 255) * self.stim_step_size
            sign = (128 - np.bitwise_and(data, 256)) / 128
            Stimdata = current_magnitude * sign

            compliance_limit = np.bitwise_and(data, 32768) != 0
            charge_recovery = np.bitwise_and(data, 16384) != 0
            amplifier_settle = np.bitwise_and(data, 8192) != 0

            stim_data_combined['Stimdata'].append(Stimdata)
            stim_data_combined['compliance_limit'].append(compliance_limit)
            stim_data_combined['charge_recovery'].append(charge_recovery)
            stim_data_combined['amplifier_settle'].append(amplifier_settle)

        return stim_data_combined

    def _read_data_trash(self, channel_data_dict, num_samples):
        """
        从所有打开的数据文件中读取指定数量的样本，并应用放大器缩放。

        此方法遍历所有已打开的数据文件描述符（self.d_fids 中的每个项），从每个文件中读取
        指定数量的样本。读取的样本是16位整数格式，代表放大器采集到的原始数据。为了将这些
        原始数据转换为实际的物理量度（例如，电压），每个样本值将乘以一个缩放因子（Intan 提
        供的样例为 0.195）。

        参数:
        - num_samples: 整数，指定要从每个文件中读取的样本数量。

        返回值:
        - 一个NumPy数组，其中包含从每个数据文件中读取的样本值，已按照放大器缩放调整。
          数组的形状为(通道数, num_samples)，每一行对应一个通道的数据。

        注意:
        - 在调用此方法之前，请确保所有需要的数据文件已经通过`open_file`方法成功打开，
          并存储在self.d_fids列表中。
        - 缩放因子0.195是根据放大器的规格预设的，样例是这个，跟随使用了这个
        """

        # 从每个amp-*.dat文件读取数据
        for i, fid in enumerate(self.d_fids):
            channel_key = f'd_{i + 1}'  # 使用通道标识作为字典键
            if fid.seekable():
                fid.seek(self.stored_samples * 2)  # 定位到上次读取的
            self.logger.debug(f"Current position in data file {i + 1} (before read): {fid.tell()}")
            d_data = np.fromfile(fid, dtype=np.int16, count=num_samples)
            channel_data_dict[channel_key] = d_data * self.d_scale
            self.logger.debug(f"Current position in data file {i + 1} (after read): {fid.tell()}")

    def calculate_available_samples(self, available_samples_total, stored_samples):
        """
        计算新数据块中可用的样本数。

        参数:
        - available_samples_total (int): 当前从数据源中检测到的可用样本总数。
        - stored_samples (int): 到目前为止已经存储到缓冲区的样本数。

        返回值:
        - int: 新数据块中可用的样本数。
        """
        return available_samples_total - stored_samples

    def _synchronize_data_streams(self, data_sources, sync_channel, tolerance_ms):
        """
        同步来自不同数据源的数据流。

        :param data_sources: dict, 包含所有数据源及其队列或缓冲区引用的字典。
        :param sync_channel: str, 用于作为基准同步的通道名称。
        :param tolerance_ms: int, 容许的时间偏差范围，单位为毫秒。

        :return: tuple, 返回同步后的数据数组和对应的时间戳数组。
        """
        import numpy as np

        # 提取基准通道的时间戳数据
        base_timestamps = data_sources[sync_channel].get_timestamps()

        # 初始化数据存储容器
        synced_data = {source: [] for source in data_sources}
        synced_timestamps = []

        # 定义时间容差，转换为采样率对应的样本数
        tolerance_samples = int(tolerance_ms * data_sources[sync_channel].sample_rate / 1000)

        # 同步所有数据源
        for base_time in base_timestamps:
            window_start = base_time - tolerance_samples
            window_end = base_time + tolerance_samples

            # 对每个数据源进行处理
            for source, buffer in data_sources.items():
                # 找到在同步窗口内的数据样本
                source_timestamps = buffer.get_timestamps()
                sync_indices = np.where((source_timestamps >= window_start) & (source_timestamps <= window_end))[0]

                if len(sync_indices) > 0:
                    # 选择最接近基准时间的样本
                    closest_idx = sync_indices[np.argmin(np.abs(source_timestamps[sync_indices] - base_time))]
                    synced_data[source].append(buffer.get_data()[closest_idx])

            # 添加同步的时间戳
            synced_timestamps.append(base_time)

        # 转换列表为数组
        for source in synced_data:
            synced_data[source] = np.array(synced_data[source])
        synced_timestamps = np.array(synced_timestamps)

        return synced_data, synced_timestamps

    def read_data(self, timespan_ms):
        """
        根据指定的时间跨度（毫秒）从数据队列中读取拼接后的二维数组和时间戳。

        参数:
        - timespan_ms: 整数，表示时间跨度，以毫秒为单位，应为100ms的整数倍。

        返回值:
        - NumPy多维数组，形状为 (通道数, 样本数)，其中样本数为 timespan_ms 转换后的样本数。
        - NumPy多维数组，形状为 (刺激通道数, 样本数)，表示对应的刺激数据。
        - NumPy数组，长度与样本数一致，表示时间戳。
        """

        self.logger.debug(f"Start read_data time: {time.time()}.")
        samples_needed = int(self.sample_rate * (timespan_ms / 1000))  # 总样本数
        blocks_needed = samples_needed // self.samples_per_100ms  # 需要的100ms块数

        collected_t = np.empty(0, dtype=np.float32)
        collected_d = np.empty((len(self.d_fids), 0), dtype=np.float32)
        collected_s = np.empty((len(self.stim_fids), 0), dtype=np.float32)  # 初始化存储刺激数据的数组

        blocks_collected = 0

        while blocks_collected < blocks_needed:
            try:
                data_type, channel_data_dict = self.data_queue.get(block=False)

                if data_type == 'data':
                    # 提取并拼接时间戳和数据
                    collected_t = np.concatenate((collected_t, channel_data_dict['t']))
                    new_data_d = np.array([channel_data_dict['d'][f'd_{i + 1}'] for i in range(len(self.d_fids))])
                    new_data_s = np.array([channel_data_dict['s'][f's_{i + 1}'] for i in range(len(self.stim_fids))])
                    collected_d = np.concatenate((collected_d, new_data_d), axis=1)
                    collected_s = np.concatenate((collected_s, new_data_s), axis=1)  # 拼接刺激数据
                    blocks_collected += 1
            except Exception as e:
                self.logger.debug(f"Error while reading data: {e}")
                break
        
        self.logger.debug(f"End read_data time: {time.time()}.")
        # 检查数据是否足够
        if collected_t.size >= samples_needed and collected_d.shape[1] >= samples_needed and collected_s.shape[
            1] >= samples_needed:
            return collected_d[:, :samples_needed], collected_s[:, :samples_needed], collected_t[:samples_needed]
        else:
            self.logger.debug("Insufficient data available")
            return None, None, None

    def read_data_inC(self, timespan_ms):
        """
        根据指定的时间跨度（毫秒）从环形缓冲区中读取拼接后的二维数组和时间戳。

        参数:
        - timespan_ms: 整数，表示时间跨度，以毫秒为单位，应为100ms的整数倍。

        返回值:
        - NumPy多维数组，形状为 (通道数, 样本数)，其中样本数为 timespan_ms 转换后的样本数。
        - NumPy多维数组，形状为 (刺激通道数, 样本数)，表示对应的刺激数据。
        - NumPy数组，长度与样本数一致，表示时间戳。
        """

        # self.logger.debug(f"Start read_data time: {time.time()}.")
        samples_needed = int(self.sample_rate * (timespan_ms / 1000))
        blocks_needed = samples_needed // self.samples_per_100ms

        collected_t = np.empty(0, dtype=np.float32)
        collected_d = np.empty((len(self.d_fids), 0), dtype=np.float32)
        collected_s = np.empty((len(self.stim_fids), 0), dtype=np.float32)

        blocks_collected = 0

        while blocks_collected < blocks_needed:
            if self.circular_buffer.size > 0:  # 确保缓冲区内有数据
                # 从环形缓冲区读取最新的数据块
                channel_data_dict = self.circular_buffer.read()

                # 提取并拼接时间戳和数据
                collected_t = np.concatenate((collected_t, channel_data_dict['t']))
                new_data_d = np.array([channel_data_dict['d'][f'd_{i + 1}'] for i in range(len(self.d_fids))])
                new_data_s = np.array([channel_data_dict['s'][f's_{i + 1}'] for i in range(len(self.stim_fids))])
                collected_d = np.concatenate((collected_d, new_data_d), axis=1)
                collected_s = np.concatenate((collected_s, new_data_s), axis=1)
                blocks_collected += 1
            else:
                # self.logger.debug("No new data available in the buffer.")
                self.logger.debug("No new data available in the buffer.")
                break

        self.logger.debug(f"End read_data time: {time.time()}.")
        # 检查数据是否足够
        if collected_t.size >= samples_needed and collected_d.shape[1] >= samples_needed and collected_s.shape[
            1] >= samples_needed:
            return collected_d[:, :samples_needed], collected_s[:, :samples_needed], collected_t[:samples_needed]
        else:
            # self.logger.debug("Insufficient data available")
            self.logger.debug("Insufficient data available")
            return None, None, None

    def get_amp_filenames(self):
        """
        获取 amp 文件名列表。

        返回值:
        - 一个包含 amp 文件名的列表。
        """
        return self.amp_filenames

    def get_stim_filenames(self):
        """
        获取 stim 文件名列表。

        返回值:
        - 一个包含 stim 文件名的列表。
        """
        return self.stim_filenames

    # plot 相关的均是测试用
    def start_plotting_task(self):
        """启动绘图线程."""

        self.plotting_thread = Thread(target=self.plotting_task)
        self.plotting_thread.start()

    def stop_plotting_task(self):
        """停止绘图线程."""

        if hasattr(self, 'plotting_thread') and self.plotting_thread.is_alive():
            self.plotting_thread.join()  # 等待线程退出

    def plotting_task(self):
        """
        使用read_data接口进行数据获取并绘制。
        维持一秒时间窗，并在绘图时标识刺激数据。
        """

        plt.ion()
        fig, ax = plt.subplots()

        # 获取初始一秒数据，直到队列非空
        while self.one_second_data_t.size < 25000:
            try:
                data_d, data_s, data_t = self.read_data(100)
                if data_d is not None and data_s is not None and data_t is not None:
                    self.update_one_second_data(data_t, data_d, data_s)
                else:
                    self.logger.debug("Insufficient initial data available, waiting for data...")
                    time.sleep(0.1)
            except Exception as e:
                self.logger.debug(f"Error while initializing one second data: {e}")
                time.sleep(0.1)

        # 逐步更新100ms数据，维护1s时间窗
        while True:
            try:
                # 每次获取100ms的数据
                new_data_d, new_data_s, new_data_t = self.read_data(100)  # 获取100ms的数据

                if new_data_d is None or new_data_s is None or new_data_t is None:
                    self.logger.debug("Insufficient data available")
                    continue

                # 更新一秒数据
                self.update_one_second_data(new_data_t, new_data_d, new_data_s)

                # 绘图
                self.plot_channels(ax, self.one_second_data_t, self.one_second_data_d, self.one_second_data_s)

                plt.pause(0.1)  # 短暂暂停以更新图形

            except Exception as e:
                self.logger.debug(f"Error in plotting task: {e}")

    def update_one_second_data(self, new_t, new_d, new_s):
        """
        更新一秒钟的数据窗口。

        参数:
        - new_t: 新的时间戳数据（100ms）。
        - new_d: 新的通道数据（100ms）。
        - new_s: 新的刺激数据（100ms）。
        """
        self.one_second_data_t = np.concatenate((self.one_second_data_t, new_t))[-25000:]
        self.one_second_data_d = np.concatenate((self.one_second_data_d, new_d), axis=1)[:, -25000:]
        self.one_second_data_s = np.concatenate((self.one_second_data_s, new_s), axis=1)[:, -25000:]

    def plot_channels(self, ax, t, d, s):
        """
        绘制多通道数据图，并标识刺激数据。

        参数:
        - ax: matplotlib的轴对象。
        - t: NumPy数组，包含每个样本的时间戳。
        - d: NumPy数组，其形状为(num_channels, num_samples)，包含所有通道的数据。
        - s: NumPy数组，其形状为(stim_channels, num_samples)，包含所有刺激数据。
        """
        ax.clear()
        num_channels = d.shape[0]
        offset_vector = np.arange(0, num_channels * self.spacing, self.spacing)
        offset_array = np.tile(offset_vector[:, None], (1, len(t)))
        d_offset = d + offset_array

        for i in range(num_channels):
            ax.plot(t, d_offset[i, :], label=f'Channel {i + 1}')

        # 标识刺激数据
        for j in range(s.shape[0]):
            stim_indices = np.nonzero(s[j, :])[0]
            for idx in stim_indices:
                ax.axvline(x=t[idx], color='r', linestyle='--', linewidth=0.5)

        ax.set_xlabel('Time (s)')
        ax.set_ylabel('Amplitude')
        ax.set_title('Real-Time Channel Data')
        ax.legend()
        plt.draw()

    def testplot(self):

        # 启动绘图线程
        self.start_plotting_task()
    
def monitor_stimulation(self, interval_ms=1000, timespan_ms=100, duration_s=10):
    """
    监控刺激数据，定期调用read_data并检查刺激数据中的非零值。

    参数:
    - interval_ms: 调用read_data的时间间隔，单位为毫秒。
    - timespan_ms: 每次调用read_data时读取的数据时间范围，单位为毫秒。
    - duration_s: 总监控时间，单位为秒。
    """
    interval_s = interval_ms / 1000  # 转换为秒
    end_time = time.time() + duration_s

    while time.time() < end_time:
        amp_data, stim_data, timestamps = self.read_data(timespan_ms)
        if stim_data is not None:
            # 返回非零元的行索引与列索引
            non_zero_stim_indices = np.nonzero(stim_data)
            if non_zero_stim_indices[0].size > 0:
                self.logger.debug("Non-zero stimulation detected:")
                for channel in np.unique(non_zero_stim_indices[0]):
                    # 基于行索引（施加刺激的通道），找寻非零元的列索引（获取刺激时刻）
                    channel_indices = non_zero_stim_indices[1][non_zero_stim_indices[0] == channel]
                    channel_values = stim_data[channel, channel_indices]
                    self.logger.debug(f"Channel {channel + 1} (indexed from 1) stimulation times and values:")
                    for time_index, value in zip(channel_indices, channel_values):
                        self.logger.debug(f"Time Index: {timestamps[time_index]}, Value: {value}")
            else:
                self.logger.debug("No stimulation detected in the given timeframe.")
        else:
            self.logger.debug("Failed to read stimulation data.")
        time.sleep(interval_s)



if __name__ == "__main__":

    directory_to_monitor1 = "E:\\TCP\\Data\\1"  # 要监控的目录
    # directory_to_monitor2 = "E:/TCP/Data/2"  # 要监控的目录

    # 初始化日志管理器
    log_manager = LogManager()
    reader = RealTimeDataReader(log_manager)
    reader.set_monitoring_directory(directory_to_monitor1)

    # a = reader.get_amp_filenames()
    # b = reader.get_stim_filenames()

    # c = reader.get_amp_filenames()
    # d = reader.get_stim_filenames()

    # 监控刺激数据
    # reader.monitor_stimulation(interval_ms=1000, timespan_ms=100, duration_s=10)

    # 测试绘图用
    # reader.testplot()

    # time.sleep(100)
    # 测试目录切换用
    # time.sleep(10)
    # reader.set_monitoring_directory(directory_to_monitor2)


