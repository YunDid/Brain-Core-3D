import time
import socket
import os

import tkinter as tk
from tkinter import filedialog


def connect_to_server(ip_address='127.0.0.1', port=5000):
    """
    连接到TCP命令服务器。

    :param ip_address: TCP服务器的IP地址。
    :param port: TCP服务器的端口号。
    :return: 连接到服务器的套接字对象。
    """

    print('Connecting to TCP command server...')
    scommand = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        scommand.connect((ip_address, port))
        print('Connected successfully.')
    except socket.error as err:
        print(f"Connection failed with error: {err}")
        scommand = None
    return scommand


def disconnect_from_server(scommand):
    """
    断开与TCP命令服务器的连接。

    :param scommand: 已连接到TCP服务器的套接字对象。
    """
    if scommand:
        print('Disconnecting from TCP command server...')
        scommand.close()
        print('Disconnected successfully.')


class InvalidControllerType(Exception):
    """Exception returned when received controller type is not
    ControllerStimRecord (this script only works with Stim systems).
    """

def get_SampleRateHertz(scommand, command_buffer_size = 1024):
    """
    获取采样率（赫兹）。

    参数:
    - scommand: socket 对象，用于与外部设备进行通信。
    - command_buffer_size: 整数，接收缓冲区大小，默认为 1024。

    返回值:
    - 整数，采样率（赫兹）。如果转换失败，返回 None。
    """

    scommand.sendall(b'get SampleRateHertz')
    command_return = str(scommand.recv(command_buffer_size), "utf-8")

    # 提取采样率
    try:
        sample_rate = int(command_return.split()[-1])
    except (IndexError, ValueError) as e:
        print(f"Error extracting sample rate: {e}")
        sample_rate = None

    return sample_rate


def verify_controller_type(scommand, command_buffer_size):
    """
    验证连接的RHX软件是否使用的是刺激/录制控制器。

    :param scommand: 已连接到TCP服务器的套接字对象。
    :param command_buffer_size: 从TCP命令套接字读取的缓冲区大小。
    :return: none
    """

    print('Verifying controller type...')
    scommand.sendall(b'get type')
    command_return = str(scommand.recv(command_buffer_size), "utf-8")
    is_stim = command_return == "Return: Type ControllerStimRecord"
    if not is_stim:
        raise InvalidControllerType(
            'This example script should only be used with a '
            'Stimulation/Recording Controller.'
        )
    print('Controller type verified as Stimulation/Recording Controller.')

def ensure_controller_stopped(scommand, command_buffer_size):
    """
    通过查询其运行模式来确保控制器没有运行。
    如果控制器正在运行，它会发送一个命令来停止它。

    :param scommand: 已连接到TCP服务器的套接字对象。
    :param command_buffer_size: 从TCP命令套接字读取的缓冲区大小。
    :return: none
    """

    print('Checking controller run mode...')
    scommand.sendall(b'get runmode')
    command_return = str(scommand.recv(command_buffer_size), "utf-8")
    is_stopped = "Return: RunMode Stop" in command_return

    if not is_stopped:
        print('Controller is running. Sending stop command...')
        scommand.sendall(b'set runmode stop')
        time.sleep(0.1)  # Give some time for the command to be processed
        print('Controller stopped.')
    else:
        print('Controller is already stopped.')


def _configureStimulation(scommand, channel, source, amplitude, duration, stimenabled, pulseTrain="SinglePulse", numberOfstimpulses=2):

    """
    生成配置通道刺激设置的命令字符串。
    此接口不对外部开放。

    :param scommand: 已连接到TCP服务器的套接字对象。此函数生成命令字符串，但不发送它；
                     调用者负责使用此套接字发送命令。
    :param channel: 要配置的通道名称，例如 'A-010'。
    :param source: 刺激的来源，例如 'keypressf1'。
    :param amplitude: 刺激电流幅度列表，分别代表第一个脉冲幅度与第二个脉冲幅度，单位为微安。 注意非负。
    :param duration: 时间列表，分别代表第一个刺激脉宽时间，第二个刺激脉宽时间，和刺激后放大器稳定时间，单位为微秒。
    :param pulseTrain: “SinglePulse” - 默认 or “PulseTrain”。
    :param numberOfstimpulses: 刺激脉冲数 0-256，仅在 pulseTrain 为 “PulseTrain” 时有效。
    :param stimenabled: 一个布尔值，指示是否启用通道的刺激（True）或禁用（False）。

    :return: 包含配置刺激通道所需的所有命令的字符串。命令用分号连接，并准备通过TCP发送。

    注意：此函数仅生成配置字符串。发送此字符串通过`scommand`套接字是调用者的责任。
         确保通过执行返回的命令上传刺激参数以使其生效。返回字符串中命令的顺序对于正确配置非常重要。
         参数可以增加，因为还有其他刺激参数可以调整，对应增加接口中指令设置即可。
         最后指令返回，主调函数负责发送指令，注意设置顺序。
    """

    # 必须有的，设置通道刺激为可触发态
    com_stimenabled = f'set {channel}.stimenabled {stimenabled};'

    # 其他指令该处插入即可
    com_source = f'set {channel}.source {source};'

    # 刺激电流幅值
    com_firstphaseamplitudemicroamps = f'set {channel}.firstphaseamplitudemicroamps {amplitude[0]};'
    com_secondphaseamplitudemicroamps = f'set {channel}.secondphaseamplitudemicroamps {amplitude[1]};'

    # 刺激脉宽
    com_firstphasedurationmicroseconds = f'set {channel}.firstphasedurationmicroseconds {duration[0]};'
    com_secondphasedurationmicroseconds = f'set {channel}.secondphasedurationmicroseconds {duration[1]};'

    # 刺激后放大器稳定时间
    com_poststimampsettlemicroseconds = f'set {channel}.poststimampsettlemicroseconds {duration[2]};'

    # 单点与高频刺激类别
    com_pulseortrain = f'set {channel}.PulseOrTrain {pulseTrain};'

    # 检查是否为高频刺激
    if pulseTrain == "PulseTrain":

        com_numberOfstimpulses = f'set {channel}.NumberOfStimPulses {numberOfstimpulses};'
        com_uploadstimparameters = f'execute uploadstimparameters {channel};'
        com_config = (com_stimenabled + com_source + com_firstphaseamplitudemicroamps + com_secondphaseamplitudemicroamps + com_firstphasedurationmicroseconds + com_secondphasedurationmicroseconds + com_poststimampsettlemicroseconds
                      + com_pulseortrain + com_numberOfstimpulses + com_uploadstimparameters)
    else:

    # 单点刺激直接上传
        com_uploadstimparameters = f'execute uploadstimparameters {channel};'
        com_config = (
                com_stimenabled + com_source + com_firstphaseamplitudemicroamps + com_secondphaseamplitudemicroamps + com_firstphasedurationmicroseconds + com_secondphasedurationmicroseconds + com_poststimampsettlemicroseconds
                + com_pulseortrain + com_uploadstimparameters)

    return com_config

def configureStimulation(scommand, channels, amplitude, duration, trigger):
    """
    为多个通道生成配置刺激设置的命令字符串。

    :param scommand: 已连接到TCP服务器的套接字对象。此函数生成命令字符串，但不发送它；
                     调用者负责使用此套接字发送命令。
    :param channels: 一个包含要配置的通道名称的列表，例如 ['A-010', 'A-011']。
    :param amplitude: 刺激电流幅度列表，分别代表第一个脉冲幅度与第二个脉冲幅度，单位为微安。 注意非负。
    :param duration: 时间列表，分别代表第一个刺激脉宽时间，第二个刺激脉宽时间，和刺激后放大器稳定时间，单位为微秒。
    :param trigger: 字符串，表示触发器 'keypressf1 - keypressf8'。

    :return: 包含配置多个通道刺激设置所需的所有命令的字符串。命令用分号连接，并准备通过TCP发送。

    注意：此函数仅生成配置字符串。发送此字符串通过`scommand`套接字是调用者的责任。
         确保通过执行返回的命令上传刺激参数以使其生效。返回字符串中命令的顺序对于正确配置非常重要。
    """

    com_configs = []

    for channel in channels:
        com_config = _configureStimulation(scommand, channel, trigger, amplitude, duration, stimenabled=True, pulseTrain="SinglePulse", numberOfstimpulses=2)
        com_configs.append(com_config)

    return ';'.join(com_configs)

def TriggerStimulation(scommand, key):
    """
    触发刺激命令。此函数发送TCP命令来触发已经配置好的刺激。

    注意：在调用此函数之前，应确保系统的运行模式已经设置为'record'或'run'。
    刺激施加后，调用方负责在合适的时刻将运行模式切换回'stop'或其他状态。

    :param scommand: 已连接到TCP服务器的套接字对象。用于发送TCP命令。
    :param key: 触发刺激的键值，例如 'keypressf1 - keypressf8'，用于指定触发特定刺激的键。
    """

    com_trigger = f'execute manualstimtriggerpulse {key};'
    scommand.sendall(com_trigger.encode())


def setFilePath(scommand, baseFileName, path):
    """
    设置文件的基本名称和路径，用于在RHX软件中保存数据文件。

    :param scommand: 已连接到TCP服务器的套接字对象。用于向RHX软件发送设置文件路径和名称的TCP命令。
    :param baseFileName: 要设置的数据文件的基本名称。这个名称将用于生成最终的数据文件名，但不包括文件扩展名。
        - 拓展名跟随文件格式的设置
        - 文件名 Intan 回默认添加时间戳，例如 最终名称为 FileName_240331_162550，年-月-日-时-分-秒
    :param path: 数据文件将被保存的目录路径。应确保这个路径已存在，并且应用程序有权写入该路径。

    在发送设置基本文件名和路径的命令之前，会先确保控制器处于停止状态。命令被合并后一起发送，以确保设置生效。
    """
    # 确保控制器已停止
    ensure_controller_stopped(scommand, COMMAND_BUFFER_SIZE)

    # 生成并发送设置基本文件名和路径的命令
    com_baseFileName = f'set filename.basefilename {baseFileName};'
    com_path = f'set filename.path {path};'
    com_setFile = com_baseFileName + com_path
    scommand.sendall(com_setFile.encode())

def setSaveFileFormat(scommand, savleTypeIndex, latencyIndex, NewDirectory, SaveWidebandAmplifierWaveforms, NewSaveFilePeriodMinutes):
    """
    设置RHX软件中保存数据文件的格式和相关配置。

    :param scommand: 已连接到TCP服务器的套接字对象。用于向RHX软件发送设置保存文件格式的TCP命令。
    :param savleTypeIndex: 文件格式索引。可选值包括 0 ("Traditional"), 1 ("OneFilePerSignalType"), 2 ("OneFilePerChannel")。
    :param latencyIndex: 写入磁盘延迟级别的索引。可选值包括 0 ("Highest"), 1 ("High"), 2 ("Medium"), 3 ("Low"), 4 ("Lowest")。
    :param NewDirectory: 是否为每次记录创建新的目录。True 或 False。
    :param SaveWidebandAmplifierWaveforms: 是否保存宽带放大器波形数据。True 或 False。
    :param NewSaveFilePeriodMinutes: 新文件创建周期，以分钟为单位。数值类型。

    在发送设置保存文件格式和相关配置的命令之前，会先确保控制器处于停止状态。命令被合并后一起发送，以确保设置生效。
    """
    # 确保控制器已停止
    ensure_controller_stopped(scommand, COMMAND_BUFFER_SIZE)

    # 设置索引列表
    FileFormat_list = ["Traditional", "OneFilePerSignalType", "OneFilePerChannel"]
    Latency_list = ["Highest", "High", "Medium", "Low", "Lowest"]

    # 生成并发送设置基本文件名和路径的命令
    com_FileFormat = f'set FileFormat {FileFormat_list[savleTypeIndex]};'
    com_Latency = f'set WriteToDiskLatency {Latency_list[latencyIndex]};'
    com_NewDirectory = f'set CreateNewDirectory {NewDirectory};'
    com_NewSaveFilePeriodMinutes = f'set NewSaveFilePeriodMinutes {NewSaveFilePeriodMinutes};'

    # 设置存储波形数据类型，其他数据类型，直接添加即可，此处以放大器数据为例.
    com_SaveWidebandAmplifierWaveforms =  f'set SaveWidebandAmplifierWaveforms {SaveWidebandAmplifierWaveforms};'

    com_setFile = com_FileFormat + com_Latency + com_NewDirectory + com_NewSaveFilePeriodMinutes + com_SaveWidebandAmplifierWaveforms
    scommand.sendall(com_setFile.encode())

def startRecord(scommand):

    scommand.sendall(b'set runmode record')
    time.sleep(0.1)  # Give some time for the command to be processed

def stopRecord(scommand):

    scommand.sendall(b'set runmode stop')
    time.sleep(0.1)  # Give some time for the command to be processed


#--------------------------------------以下接口不必在意--------------------------------------

def __configureTrainStimulation(scommand, channel, source, amplitude, duration, pulseTrain, numberOfstimpulses, stimenabled = False):

    """
    生成配置单个通道高频刺激设置的命令字符串，例如设置为 256 个刺激脉冲的序列。
    此接口不对外部开放。

    :param scommand: 已连接到TCP服务器的套接字对象。此函数生成命令字符串，但不发送它；
                     调用者负责使用此套接字发送命令。
    :param channel: 要配置的通道名称，例如 'A-010'。
    :param source: 刺激的来源，例如 'keypressf1'。
    :param amplitude: 刺激第一阶段的幅度，单位为微安。
    :param duration: 刺激第一阶段的持续时间，单位为微秒。
    :param pulseTrain: “SinglePulse” - 默认 or “PulseTrain”
    :param train_num: 刺激脉冲数 0-256。
    :param stimenabled: 一个布尔值，指示是否启用通道的刺激（True）或禁用（False）。

    :return: 包含配置刺激通道所需的所有命令的字符串。命令用分号连接，并准备通过TCP发送。

    注意：此函数仅生成配置字符串。发送此字符串通过`scommand`套接字是调用者的责任。
         确保通过执行返回的命令上传刺激参数以使其生效。返回字符串中命令的顺序对于正确配置非常重要。
         参数可以增加，因为还有其他刺激参数可以调整，对应增加接口中指令设置即可.
         最后指令返回，主调函数负责发送指令，注意设置顺序.
    """


    # 必须有的，设置通道刺激为可触发态.
    com_stimenabled = f'set {channel}.stimenabled {stimenabled};'


    # 其他指令该处插入即可.
    com_source = f'set {channel}.source {source};'

    com_firstphaseamplitudemicroamps = f'set {channel}.firstphaseamplitudemicroamps {amplitude};'

    com_firstphasedurationmicroseconds = f'set {channel}.firstphasedurationmicroseconds {duration};'

    com_PulseOrTrain = f'set {channel}.PulseOrTrain {pulseTrain};'

    com_numberOfstimpulses = f'set {channel}.NumberOfStimPulses {numberOfstimpulses};'

    # 必须上传后才能生效.
    com_uploadstimparameters = f'execute uploadstimparameters {channel};'

    com_config = com_stimenabled + com_source + com_firstphaseamplitudemicroamps + com_firstphasedurationmicroseconds + com_PulseOrTrain + com_numberOfstimpulses + com_uploadstimparameters;

    return com_config


def __configureSingleStimulation(scommand, channel, source, amplitude, duration, stimenabled = False):

    """
    生成配置单个通道刺激设置的命令字符串。
    此接口不对外部开放。

    :param scommand: 已连接到TCP服务器的套接字对象。此函数生成命令字符串，但不发送它；
                     调用者负责使用此套接字发送命令。
    :param channel: 要配置的通道名称，例如 'A-010'。
    :param source: 刺激的来源，例如 'keypressf1'。
    :param amplitude: 刺激第一阶段的幅度，单位为微安。
    :param duration: 刺激第一阶段的持续时间，单位为微秒。
    :param stimenabled: 一个布尔值，指示是否启用通道的刺激（True）或禁用（False）。

    :return: 包含配置刺激通道所需的所有命令的字符串。命令用分号连接，并准备通过TCP发送。

    注意：此函数仅生成配置字符串。发送此字符串通过`scommand`套接字是调用者的责任。
         确保通过执行返回的命令上传刺激参数以使其生效。返回字符串中命令的顺序对于正确配置非常重要。
         参数可以增加，因为还有其他刺激参数可以调整，对应增加接口中指令设置即可.
         最后指令返回，主调函数负责发送指令，注意设置顺序.
    """


    # 必须有的，设置通道刺激为可触发态.
    com_stimenabled = f'set {channel}.stimenabled {stimenabled};'

    # 其他指令该处插入即可.
    com_source = f'set {channel}.source {source};'

    com_firstphaseamplitudemicroamps = f'set {channel}.firstphaseamplitudemicroamps {amplitude};'

    com_firstphasedurationmicroseconds = f'set {channel}.firstphasedurationmicroseconds {duration};'

    # 必须上传后才能生效.
    com_uploadstimparameters = f'execute uploadstimparameters {channel};'

    com_config = com_stimenabled + com_source + com_firstphaseamplitudemicroamps + com_firstphasedurationmicroseconds + com_uploadstimparameters;

    return com_config

def __configureSingleStimulation2(scommand, channel, source, amplitude, duration, stimenabled = False):

    """
    生成配置单个通道刺激设置的命令字符串。
    此接口不对外部开放。

    :param scommand: 已连接到TCP服务器的套接字对象。此函数生成命令字符串，但不发送它；
                     调用者负责使用此套接字发送命令。
    :param channel: 要配置的通道名称，例如 'A-010'。
    :param source: 刺激的来源，例如 'keypressf1'。
    :param amplitude: 刺激第一阶段的幅度，单位为微安。
    :param duration: 刺激第一阶段的持续时间，单位为微秒。
    :param stimenabled: 一个布尔值，指示是否启用通道的刺激（True）或禁用（False）。

    :return: 包含配置刺激通道所需的所有命令的字符串。命令用分号连接，并准备通过TCP发送。

    注意：此函数仅生成配置字符串。发送此字符串通过`scommand`套接字是调用者的责任。
         确保通过执行返回的命令上传刺激参数以使其生效。返回字符串中命令的顺序对于正确配置非常重要。
         参数可以增加，因为还有其他刺激参数可以调整，对应增加接口中指令设置即可.
         最后指令返回，主调函数负责发送指令，注意设置顺序.
    """


    # 必须有的，设置通道刺激为可触发态.
    com_stimenabled = f'set {channel}.stimenabled {stimenabled};'

    # 其他指令该处插入即可.
    com_source = f'set {channel}.source {source};'

    com_firstphaseamplitudemicroamps = f'set {channel}.firstphaseamplitudemicroamps {amplitude};'

    com_firstphasedurationmicroseconds = f'set {channel}.firstphasedurationmicroseconds {duration};'

    # 必须上传后才能生效.
    com_uploadstimparameters = f'execute uploadstimparameters {channel};'

    # com_config = com_stimenabled + com_source + com_firstphaseamplitudemicroamps + com_firstphasedurationmicroseconds + com_uploadstimparameters;

    com_config = com_stimenabled + com_source + com_firstphaseamplitudemicroamps + com_firstphasedurationmicroseconds;


    return com_config


# -----------------------------------------------Test-----------------------------------------------


def TestDemo2(scommand):
    baseFileName = f"FileName"
    path = f"E:/TCP/Data"

    setFilePath(scommand, baseFileName, path)
    setSaveFileFormat(scommand,2,4,True,True,5)

    startRecord(scommand)
    time.sleep(2)
    stopRecord(scommand)

def TestDemo4(scommand):
    channels = ['A-000','A-001','A-002','A-003']
    channels2 = ['A-004', 'A-005', 'A-006', 'A-007']

    command = configureStimulation(scommand,channels, [100,100], [200,200,10], "KeyPressF1")
    scommand.sendall(command.encode())

    command += configureStimulation(scommand, channels2, [50,50], [200, 200, 10], "KeyPressF2")
    scommand.sendall(command.encode())

    scommand.sendall(b'set runmode run;')

    time.sleep(1)

    TriggerStimulation(scommand, 'KeyPressF1')

    time.sleep(3)

    TriggerStimulation(scommand, 'KeyPressF2')



def TestDemo1(scommand):

    channels1 = ['A-000']
    channels = ['A-001']

    command = __configureSingleStimulation(scommand, channels1[0], 'keypressf1', 10, 500, True)
    scommand.sendall(command.encode())

    scommand.sendall(b'set runmode run;')

    # time.sleep(2)
    #
    # command = __configureSingleStimulation(scommand, channels[0], 'keypressf2', 9, 9, True)
    # scommand.sendall(command.encode())
    #
    # command = __configureSingleStimulation(scommand, channels[0], 'keypressf3', 8, 8, True)
    # scommand.sendall(command.encode())
    #
    # command = __configureSingleStimulation(scommand, channels[0], 'keypressf4', 7, 7, True)
    # scommand.sendall(command.encode())
    #
    # command = __configureSingleStimulation(scommand, channels[0], 'keypressf5', 6, 6, True)
    # scommand.sendall(command.encode())
    #
    # command = __configureSingleStimulation(scommand, channels[0], 'keypressf6', 5, 5, True)
    # scommand.sendall(command.encode())

    # TriggerStimulation(scommand,'keypressf1')

def TestDemo6(scommand):

    channels1 = ['A-000']
    channels2 = ['A-001']

    command = __configureSingleStimulation(scommand, channels1[0], 'KeyPressF1', 3, 2, True)
    scommand.sendall(command.encode())

    scommand.sendall(b'set runmode run;')

    time.sleep(5)

    command = __configureSingleStimulation(scommand, channels1[0], 'KeyPressF2', 300, 200, True)
    scommand.sendall(command.encode())

    # command = __configureSingleStimulation(scommand, channels2[0], 'keypressf2', 3, 100, True)
    # scommand.sendall(command.encode())

def TestDemo3(scommand):
    print(get_SampleRateHertz(scommand, 1024))


def RunAndStimulateDemo():

    # Connect to TCP command server - default home IP address at port 5000
    scommand = connect_to_server()
    # Query controller type from RHX software.
    # Throw an error and exit if controller type is not Stim.
    verify_controller_type(scommand, COMMAND_BUFFER_SIZE)
    # Query runmode from RHX software
    ensure_controller_stopped(scommand, COMMAND_BUFFER_SIZE)
    # -----------------------------------------------------------------------------------------
    # Test1
    # TestDemo1(scommand)
    # Test2
    # TestDemo2(scommand)
    # Test3
    # TestDemo3(scommand)
    # Test4
    TestDemo4(scommand)
    # Test6
    # TestDemo6(scommand)
    # -----------------------------------------------------------------------------------------
    # Close TCP socket
    disconnect_from_server(scommand)

if __name__ == '__main__':
    # Declare buffer size for reading from TCP command socket
    # This is the maximum number of bytes expected for 1 read. 1024 is plenty
    # for a single text command.
    # Increase if many return commands are expected.
    COMMAND_BUFFER_SIZE = 1024

    RunAndStimulateDemo()

