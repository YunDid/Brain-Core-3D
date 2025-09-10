from log_manager import LogManager

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

        # 使用统一的日志管理器
        self.logger = LogManager.get_logger("CircularBuffer")

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

        # 计算当前读取索引与最新索引的差距，考虑回绕情况
        gap = (latest_index - self.read_index + self.capacity) % self.capacity

        # 如果差距太大，说明读取落后太多，需要跳跃
        base_gap = 5
        if gap > base_gap:  # 例如，如果落后超过500ms，则更新指针
            self.logger.debug("已积累500ms延迟，需要进行修正")
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