import os
import numpy as np

def read_stimulation_file(filename, stim_step_size):
    """
    读取并解析刺激事件文件。

    参数:
    - filename: 刺激事件文件的路径。
    - stim_step_size: 用于转换电流值的步长大小。

    返回值:
    - 一个字典，包含电流值和状态位信息。
    """
    try:
        # 打开文件并读取内容
        with open(filename, 'rb') as f:  # 以二进制模式打开文件
            file_size = os.path.getsize(filename)  # 获取文件大小（字节）
            num_samples = file_size // 2  # 计算样本数量，每个样本为2字节
            data = np.fromfile(f, dtype=np.uint16, count=num_samples)  # 读取文件内容并解析为16位无符号整数数组

        # 解析电流值
        current_magnitude = np.bitwise_and(data, 255) * stim_step_size  # 提取最低8位并乘以步长大小，得到电流幅值
        sign = (128 - np.bitwise_and(data, 256)) / 128  # 提取第9位（符号位），将其转换为符号（正或负）
        current = current_magnitude * sign  # 计算实际电流值

        # 解析状态位
        compliance_limit = np.bitwise_and(data, 32768) != 0  # 提取第16位，判断是否达到合规限制
        charge_recovery = np.bitwise_and(data, 16384) != 0  # 提取第15位，判断是否激活充电恢复
        amplifier_settle = np.bitwise_and(data, 8192) != 0  # 提取第14位，判断是否激活放大器稳定

        # 存储解析结果
        stim_data = {
            'current': current,  # 电流值
            'compliance_limit': compliance_limit,  # 合规限制状态
            'charge_recovery': charge_recovery,  # 充电恢复状态
            'amplifier_settle': amplifier_settle  # 放大器稳定状态
        }

        return stim_data

    except Exception as e:
        print(f"读取刺激文件 {filename} 时出错: {e}")
        return None

# 示例使用
filename = 'E:\\TCP\\Data\\1\\FFF_240629_133456\\stim-B-002.dat'
stim_step_size = 1  # 示例步长大小，请根据实际情况调整
stim_data = read_stimulation_file(filename, stim_step_size)

if stim_data:
    print("刺激数据:")
    current_non_zero_indices = np.nonzero(stim_data['current'])[0]  # 找出非零元素的索引
    current_non_zero_values = stim_data['current'][current_non_zero_indices]  # 获取非零元素的值

    # 打印非零电流值及其索引（样本标签）
    for index, value in zip(current_non_zero_indices, current_non_zero_values):
        print(f"样本标签: {index}, 电流值: {value}")

    print("电流:", len(stim_data['current']))
    print("合规限制:", stim_data['compliance_limit'])
    print("充电恢复:", stim_data['charge_recovery'])
    print("放大器稳定:", stim_data['amplifier_settle'])

