from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import numpy as np
import os
import time
import matplotlib.pyplot as plt
import struct  # 用于处理二进制数据
from threading import Thread, Event
from queue import Queue
import sys

from TestRead import ReadIntanDataThread

class FileMonitorHandler(FileSystemEventHandler):
    # 文件监控类
    def __init__(self, reader):
        self.reader = reader

    def on_created(self, event):
        if event.is_directory:
            # 新目录创建，打印路径，但不在这里处理
            print(f"New directory created: {event.src_path}")
        if any(event.src_path.endswith(ext) for ext in ['.dat', '.rhs']):
            self.reader.handle_new_file(event.src_path)

class RealTimeDataReader:
    # 数据实时读取类
    def __init__(self):

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
        self.sample_rate = None
        # 文件描述符列表.
        self.d_fids = []
        # 时间戳数据文件描述符
        self.t_fid = None
        # ----------------------缓冲池设置相关-----------------------
        self.data_queue = Queue()
        self.min_samples_per_read = 1000
        # 已追踪样本数
        self.stored_samples = 0
        # 放大器数据数据缩放
        self.d_scale = 0.195

        # ----------------------绘图测试相关-----------------------
        self.plotting_thread = None
        self.spacing = 1000 # 绘图测试用 通道垂直间距

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

    def start_monitoring(self):
        """初始化并启动文件监视器。"""

        if not self.monitor_running:
            self.observer = Observer()
            event_handler = FileMonitorHandler(self)
            self.observer.schedule(event_handler, self.directory_to_monitor, recursive=True)
            self.observer.start()
            self.monitor_running = True
            print("Started monitoring directory:", self.directory_to_monitor)

    def stop_monitoring(self):
        """停止文件监视器并等待其完全停止。"""

        if self.monitor_running:
            self.observer.stop()  # 告诉observer停止监控
            self.observer.join()  # 等待监控线程完全停止
            self.observer.unschedule_all()  # 清除所有监控任务
            self.monitor_running = False
            print("Stopped monitoring directory:", self.directory_to_monitor)

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

    def data_loading_task(self):
        """
        数据加载任务的主循环。

        此方法作为一个后台线程运行，负责周期性地检查是否有新的数据文件就绪
        并从这些文件中读取数据。读取的数据会被缩放并以字典的形式放入队列中，
        供其他部分的代码进一步处理。

        方法执行流程：
        1. 检查是否所有必需的文件描述符都已就绪。如果没有，等待0.1秒后再次检查。
        2. 计算当前可用的样本数量，基于时间戳文件和数据文件的大小。
        3. 如果可用的样本数量大于设定的缓冲大小，则从文件中读取这些样本。
        4. 从时间戳文件中读取时间戳数据，并从每个数据文件中读取样本数据。
        5. 将读取的数据缩放后存入字典，然后将字典放入队列中。
        6. 更新已存储的样本数量，并打印当前队列的状态信息。

        参数:
        无

        返回值:
        无

        注意:
        - 在数据文件格式或采样率发生变化时，需要相应地调整读取和缩放逻辑。
        """

        while self.loading_running:
            try:
                # 首先检查所有必需的文件描述符是否就绪
                if not self.ready_to_load:
                    time.sleep(0.1)  # 如果必需的文件尚未就绪，则等待
                    continue

                # 计算可用样本数   
                available_samples_t = os.path.getsize(self.timestamp_filename) // 4
                available_samples_d = min(os.path.getsize(f) // 2 for f in self.filenames) if self.filenames else 0
                available_samples = min(available_samples_t, available_samples_d)

                num_samples = available_samples - self.stored_samples

                # 直接按照可用样本数进行数据读取
                if num_samples > self.min_samples_per_read:
                    # 计算本次应读取的样本数
                    # num_samples = available_samples - self.plotted_samples

                    # 创建一个字典用于存储每个通道的数据
                    channel_data_dict = {'t': None, 'd': None}
                    channel_data_dict['t'] = self.read_timestamp(num_samples)
                    self.read_data(channel_data_dict, num_samples)

                    # 将整个字典放入队列
                    self.data_queue.put(('data', channel_data_dict))
                    self.stored_samples += num_samples
                    print(f"current_storeing_samples : {num_samples}")
                    print(f"stored_samples : {self.stored_samples}")

                    size_of_dict = sys.getsizeof(channel_data_dict)
                    # print(f"num_samples : {num_samples}")
                    print(f"Size of the queue: {self.data_queue.qsize()}\n")
                else:
                    time.sleep(0.1)  # 如果没有可用的样本，稍等一会儿再检查
            except Exception as e:
                print(f"Error loading data: {e}")
                time.sleep(0.1)

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
            print(f"Switching to new directory: {new_subdirectory}")
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
        - filename: 新创建或修改的文件的完整路径。

        返回值:
        无
        """

        # 检查是否为新目录并作处理.
        self.handle_new_subdirectory(filename)

        # 检测并处理新文件
        if filename not in self.filenames:
            if 'time.dat' in filename:
                self.timestamp_filename = filename
                self.t_fid = self.open_file(filename)
            elif 'info.rhs' in filename:
                self.read_sample_rate_from_info_file(filename)
            elif filename.endswith('.dat'):
                self.filenames.append(filename)
                print(f"Added file: {filename}")
                self.d_fids.append(self.open_file(filename))

        # 检查是否所有必需的文件都已添加
        if self.t_fid and self.d_fids and self.sample_rate:
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

        print(f"Updating monitoring directory to: {new_directory}")

        # 使用self.monitor_running来判断监控线程的状态
        if self.monitor_running:
            # 调用stop_monitoring方法来停止当前的监控活动
            self.stop_monitoring()
            print("Directory monitoring stopped.")

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
        # 清空数据队列
        # 注意存在丢失问题，所以何时清空缓冲池中数据后续需要考虑.
        with self.data_queue.mutex:
            self.data_queue.queue.clear()
        # 更新追踪样本数
        self.stored_samples = 0

        # 重新启动监控到新的目录
        self.start_monitoring()
        print("Monitoring restarted for the new directory.")

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
            print(f"Failed to read {info_filename}. Is it in this directory?")

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
            print(f"Failed to read {filename}. Is it in this directory?")
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

    def read_timestamp(self, num_samples):
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

        print(f"Current position in timestamp file (before read): {self.t_fid.tell()}")
        t_data = np.fromfile(self.t_fid, dtype=np.int32, count=num_samples)
        print(f"Current position in timestamp file (after read): {self.t_fid.tell()}")

        return t_data / self.sample_rate

    def read_data(self, channel_data_dict, num_samples):
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
            print(f"Current position in data file {i + 1} (before read): {fid.tell()}")
            d_data = np.fromfile(fid, dtype=np.int16, count=num_samples)
            channel_data_dict[channel_key] = d_data * self.d_scale
            print(f"Current position in data file {i + 1} (after read): {fid.tell()}")

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
        测试缓冲池使用缓冲池数据的绘图任务。

        此方法持续从数据队列中获取数据，并调用plot_channels方法来绘制每个通道的数据图形。
        在后台线程中持续运行的，不断地监视数据队列，一旦队列中有新的数据，
        就立即处理并绘制。此方法能够确保实时数据可视化的连续性和流畅性。

        参数:
        无

        返回值:
        无

        注意:
        - 本方法使用了阻塞队列操作`self.data_queue.get`，带有超时设置来避免永久阻塞。
        - 如果从队列中成功获取到数据，会检查数据类型是否为`'data'`，只处理数据类型为`'data'`的项。
        - 对于每批获取的数据，会计算通道数量，创建一个适当大小的NumPy数组来存储合并的通道数据，
          然后调用`plot_channels`方法进行绘图。
        - 这个使用方法并不好，是直接把整块数据拿出来处理（绘制），之后对于缓冲池的处理方式需要斟酌后修改，这个仅供参考测试.

        示例用法:
        - 通常，创建一个线程来运行这个方法，以实现数据的实时绘图：
            threading.Thread(target=reader.plotting_task).start()
        """
        while True:
            try:
                # 从队列中获取数据
                data_type, channel_data_dict = self.data_queue.get(block=True, timeout=1.0)

                if data_type == 'data':
                    # 解析字典中的数据
                    t = channel_data_dict['t']
                    # 调用堆栈中发现 channel_data_dict 中除了时间戳通道，还有一个不知道什么类型的数据，剔除掉.
                    # 就是说 64 通道的数据块应该是 64个通道数据 + 1个时间戳 65个数据，但是实际有66个数据..
                    num_channels = len(channel_data_dict) - 2  # 减去时间戳通道
                    d_combined = np.zeros((num_channels, len(t)))

                    for i in range(1, num_channels + 1):
                        channel_key = f'd_{i}'
                        d_combined[i - 1, :] = channel_data_dict[channel_key]

                    # 数据处理和绘图逻辑
                    self.plot_channels(t, d_combined)

            except Exception as e:
                print(f"Error in plotting task: {e}")

    def plot_channels(self, t, d):
        """
        绘制多通道数据图。

        此方法根据提供的时间戳和数据绘制每个通道的图形。为了在同一幅图上区分不同通道的数据，
        每个通道的数据将被垂直偏移。这使得所有通道的数据可以清晰地展示在同一幅图上，而不会发生重叠。

        参数:
        - t: NumPy数组，包含每个样本的时间戳。一个数据块内的所有通道共享相同的时间戳。
        - d: NumPy数组，其形状为(num_channels, num_samples)，包含所有通道的数据。
             每一行代表一个通道的样本数据。

        返回值:
        无
        """

        num_channels = d.shape[0]
        offset_vector = np.arange(0, num_channels * self.spacing, self.spacing)
        offset_array = np.tile(offset_vector[:, None], (1, len(t)))
        d_offset = d + offset_array

        # 清除现有图像并绘制新数据
        plt.clf()
        for i in range(num_channels):
            plt.plot(t, d_offset[i, :])

        plt.xlabel('Time (s)')
        plt.ylabel('Amplitude')
        plt.title('Real-Time Channel Data')
        plt.draw()
        plt.pause(0.01)  # 短暂暂停以更新图形

    def testplot(self):

        # 初始化绘图
        plt.ion()
        plt.figure()

        # 启动绘图线程
        self.start_plotting_task()


if __name__ == "__main__":

    directory_to_monitor1 = "E:/TCP/Data/1"  # 要监控的目录
    # directory_to_monitor2 = "E:/TCP/Data/2"  # 要监控的目录
    reader = RealTimeDataReader()
    reader.set_monitoring_directory(directory_to_monitor1)

    ReadIntanDataThread(reader)

    # 测试目录切换用
    # time.sleep(10)
    # reader.set_monitoring_directory(directory_to_monitor2)

    # 测试绘图用
    # reader.testplot()
