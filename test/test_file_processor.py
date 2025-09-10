# test_file_processor.py
import unittest
import tempfile
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_processor import FileProcessor, FileInfo

class TestFileProcessor(unittest.TestCase):
    
    def setUp(self):
        self.processor = FileProcessor()
    
    def test_identify_file_type(self):
        """测试文件类型识别"""
        # 测试各种文件名
        self.assertEqual(self.processor._identify_file_type('time.dat'), 'timestamp')
        self.assertEqual(self.processor._identify_file_type('info.rhs'), 'info')
        self.assertEqual(self.processor._identify_file_type('amp-A-001.dat'), 'amp')
        self.assertEqual(self.processor._identify_file_type('stim-A-001.dat'), 'stim')
        self.assertEqual(self.processor._identify_file_type('board-DIGITAL-IN-01.dat'), 'digital_in')
        self.assertEqual(self.processor._identify_file_type('unknown.txt'), None)
    
    def test_file_count(self):
        """测试文件计数功能"""
        # 模拟添加文件
        with tempfile.NamedTemporaryFile(suffix='.dat', delete=False) as f:
            f.write(b'test data')
            f.flush()
            
            # 重命名为amp文件
            amp_file = f.name.replace('.dat', '_amp-A-001.dat')
            os.rename(f.name, amp_file)
            
            # 处理文件
            file_info = self.processor.process_new_file(amp_file)
            self.assertIsNotNone(file_info)
            self.assertEqual(file_info.file_type, 'amp')
            
            # 检查计数
            self.assertEqual(self.processor.get_file_count_by_type('amp'), 1)
            self.assertEqual(self.processor.get_file_count_by_type('stim'), 0)
            
            # 清理
            os.unlink(amp_file)

if __name__ == '__main__':
    unittest.main()