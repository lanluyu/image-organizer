# coding:utf-8
import os
import shutil
import hashlib
import subprocess
import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from PIL import Image, ExifTags

class ImageTool:
    """
    iPhone 图片/视频处理工具类
    
    功能说明：
    1. 遍历指定源文件夹中的所有文件。
    2. 使用 MD5 哈希算法判断文件是否重复（基于内容）。
    3. 如果发现重复文件，将其重命名并移动到指定的"重复文件"文件夹，保留原始文件的一个副本。
    4. 如果是非重复文件，根据其拍摄时间或创建时间（优先读取 Exif 信息），按"年份/月份"的结构归档到目标文件夹。
    5. 特别处理 .AAE 文件：解析 XML 获取时间，并按相同逻辑归档。
    """

    def __init__(self, source_dir, target_dir, duplicate_dir, exiftool_path=None):
        """
        初始化工具
        
        :param source_dir: 待处理的源文件夹路径 (包含图片/视频的文件夹)
        :param target_dir: 处理后正常文件的存放目标文件夹 (将按年月分类)
        :param duplicate_dir: 重复文件的存放文件夹
        :param exiftool_path: exiftool.exe 的绝对路径。如果不提供，尝试在当前脚本目录下的 exiftool 文件夹中查找。
        """
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)
        self.duplicate_dir = Path(duplicate_dir)
        
        # 配置日志
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)

        # 设置 ExifTool 路径
        if exiftool_path:
            self.exiftool_path = Path(exiftool_path)
        else:
            # 默认查找当前目录下的 exiftool/exiftool.exe
            self.exiftool_path = Path(__file__).parent / "exiftool" / "exiftool.exe"
        
        if not self.exiftool_path.exists():
            self.logger.warning(f"Exiftool 未找到: {self.exiftool_path}。将尝试使用 PIL 读取图片时间，视频时间可能不准确。")
            self.exiftool_path = None
        else:
            self.logger.info(f"使用 ExifTool: {self.exiftool_path}")

        # 确保存放目录存在
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.duplicate_dir.mkdir(parents=True, exist_ok=True)
        
        # 用于记录本次运行中已处理文件的哈希值，防止源文件夹内部有重复
        self.processed_hashes = set()

    def get_file_hash(self, file_path, block_size=65536):
        """
        计算文件的 MD5 哈希值，用于唯一标识文件内容。
        使用分块读取，防止大文件占用过多内存。
        
        :param file_path: 文件路径
        :param block_size: 读取块大小
        :return: MD5 哈希字符串
        """
        md5 = hashlib.md5()
        try:
            with open(file_path, 'rb') as f:
                for block in iter(lambda: f.read(block_size), b''):
                    md5.update(block)
            return md5.hexdigest()
        except Exception as e:
            self.logger.error(f"计算哈希出错 {file_path}: {e}")
            return None

    def get_date_from_aae(self, file_path):
        """
        从 AAE (XML) 文件中解析时间。
        """
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
            # 查找 <date> 元素
            date_element = root.find('.//date')
            if date_element is not None:
                # 格式通常为: 2023-01-01T12:00:00Z
                date_str = date_element.text.replace('T', ' ').replace('Z', '')
                return datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        except Exception as e:
            self.logger.warning(f"解析 AAE 文件失败 {file_path}: {e}")
        return None

    def get_date_from_pil(self, file_path):
        """
        使用 PIL (Pillow) 库尝试读取图片 Exif 中的拍摄时间。
        作为 ExifTool 的备选方案。
        """
        try:
            if file_path.suffix.lower() not in ['.jpg', '.jpeg', '.png', '.tiff', '.webp']:
                return None
                
            img = Image.open(file_path)
            exif_data = img._getexif()
            if not exif_data:
                return None
            
            # 查找 DateTimeOriginal (Tag ID: 36867) 或 DateTime (Tag ID: 306)
            date_str = exif_data.get(36867) or exif_data.get(306)
            
            if date_str:
                return datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
        except Exception:
            # PIL 读取失败静默处理，交给后续逻辑
            pass
        return None

    def get_file_date(self, file_path):
        """
        获取文件的拍摄时间或创建时间。
        
        策略：
        1. 对于 .AAE 文件，解析 XML 内容。
        2. 对于其他文件，优先尝试调用 ExifTool (支持 -G 选项获取精确分组信息)。
           参考优先级: EXIF:DateTimeOriginal > EXIF:CreateDate > XMP:CreateDate > XMP:ModifyDate > File:FileModifyDate
        3. 如果 ExifTool 失败，尝试使用 PIL 读取 (仅限图片)。
        4. 如果都失败，回退到操作系统的文件创建时间 (ctime)。
        
        :param file_path: 文件路径
        :return: datetime 对象
        """
        # 1. AAE 文件特殊处理
        if file_path.suffix.lower() == '.aae':
            date_obj = self.get_date_from_aae(file_path)
            if date_obj:
                return date_obj
            # 如果解析失败，继续后续流程 (虽然 ExifTool 可能也读不出 AAE 的 Exif，但 FileModifyDate 可用)

        date_obj = None

        # 2. 尝试使用 ExifTool (针对图片和视频)
        if self.exiftool_path:
            try:
                # 构造命令
                # -G: 输出 Tag 组名 (如 EXIF:DateTimeOriginal)
                # -j: 输出 JSON
                cmd = [
                    str(self.exiftool_path),
                    '-j', 
                    '-G', 
                    '-charset', 'filename=utf8',
                    '-DateTimeOriginal',
                    '-CreateDate',
                    '-CreationDate',
                    '-MediaCreateDate',
                    '-TrackCreateDate',
                    '-ContentCreateDate',
                    '-ModifyDate',
                    '-FileModifyDate',
                    str(file_path)
                ]
                
                # Windows 下隐藏弹出的命令行窗口
                startupinfo = None
                if os.name == 'nt':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
                result = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo, encoding='utf-8')
                
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    if data and len(data) > 0:
                        info = data[0]
                        
                        # 按照指定优先级获取时间
                        # 注意：ExifTool 的 JSON 输出在使用 -G 时，键名会包含组名
                        
                        date_str = (
                            info.get('EXIF:DateTimeOriginal') or 
                            info.get('EXIF:CreateDate') or 
                            info.get('QuickTime:CreateDate') or # 视频常见
                            info.get('QuickTime:MediaCreateDate') or
                            info.get('QuickTime:TrackCreateDate') or
                            info.get('QuickTime:CreationDate') or
                            info.get('XMP:CreateDate') or
                            info.get('XMP:ModifyDate') or
                            info.get('File:FileModifyDate') or
                            # 兜底：尝试不带组名的字段 (防止 ExifTool 版本差异)
                            info.get('DateTimeOriginal') or
                            info.get('CreateDate') or
                            info.get('CreationDate') or
                            info.get('ModifyDate')
                        )
                        
                        if date_str:
                            # ExifTool 日期通常格式: "YYYY:MM:DD HH:MM:SS"
                            # 有时后面会带时区如 "+08:00"，这里只取前19位
                            try:
                                date_obj = datetime.strptime(date_str[:19], '%Y:%m:%d %H:%M:%S')
                                logging.info(f'获取照片时间: {date_obj}')
                            except ValueError:
                                pass 
                else:
                    self.logger.warning(f"ExifTool 调用返回错误 {file_path}: {result.stderr}")
            except Exception as e:
                self.logger.warning(f"ExifTool 执行异常 {file_path}: {e}")

        # 3. 如果 ExifTool 没拿到时间，尝试 PIL (仅图片)
        if not date_obj:
            date_obj = self.get_date_from_pil(file_path)

        # 4. 降级方案：使用文件系统的创建时间
        if not date_obj:
            try:
                timestamp = os.path.getctime(file_path)
                date_obj = datetime.fromtimestamp(timestamp)
                self.logger.info(f"   [提示] 无法读取拍摄时间，使用文件创建时间: {file_path.name} -> {date_obj}")
            except Exception as e:
                self.logger.error(f"获取文件属性失败 {file_path}: {e}")
                date_obj = datetime.now() # 极端情况使用当前时间

        return date_obj

    def get_unique_path(self, directory, filename):
        """
        生成唯一的目标文件路径。
        如果目标文件夹下已存在同名文件，则在文件名后追加 _1, _2 等后缀。
        
        :param directory: 目标文件夹路径 (Path对象)
        :param filename: 原始文件名
        :return: 不重复的完整路径 (Path对象)
        """
        name = Path(filename).stem
        suffix = Path(filename).suffix
        counter = 1
        target_path = directory / filename
        
        while target_path.exists():
            target_path = directory / f"{name}_{counter}{suffix}"
            counter += 1
        return target_path

    def process_files(self):
        """
        执行主处理流程：遍历、查重、归档。
        """
        self.logger.info(f"开始处理文件夹: {self.source_dir}")
        
        if not self.source_dir.exists():
            self.logger.error(f"源文件夹不存在: {self.source_dir}")
            return

        count_moved = 0
        count_duplicate = 0
        
        # os.walk 遍历源文件夹及其子文件夹
        for root, dirs, files in os.walk(self.source_dir):
            for file in files:
                file_path = Path(root) / file
                
                # 忽略隐藏文件 (如 .DS_Store)
                if file.startswith('.'):
                    continue

                self.logger.info(f"正在处理: {file}")
                
                # 步骤 1: 计算哈希
                file_hash = self.get_file_hash(file_path)
                if not file_hash:
                    continue # 读取失败跳过

                # 步骤 2: 判断是否重复
                if file_hash in self.processed_hashes:
                    # --- 发现重复文件 ---
                    self.logger.info(f"-> 发现重复文件，准备移入重复目录: {file}")
                    
                    # 获取在 duplicate_dir 中的唯一路径
                    target_path = self.get_unique_path(self.duplicate_dir, file)
                    
                    try:
                        shutil.move(file_path, target_path)
                        self.logger.info(f"   已移动重复文件到: {target_path}")
                        count_duplicate += 1
                    except Exception as e:
                        self.logger.error(f"   移动重复文件失败: {e}")
                
                else:
                    # --- 发现新文件 ---
                    self.processed_hashes.add(file_hash)
                    
                    # 步骤 3: 获取日期并归档
                    date_obj = self.get_file_date(file_path)
                    year_str = str(date_obj.year)
                    month_str = f"{date_obj.month:02d}"
                    
                    # 创建 年/月 文件夹结构
                    year_month_dir = self.target_dir / year_str / month_str
                    year_month_dir.mkdir(parents=True, exist_ok=True)
                    
                    # 获取在目标目录中的唯一路径
                    target_path = self.get_unique_path(year_month_dir, file)
                    
                    try:
                        shutil.move(file_path, target_path)
                        self.logger.info(f"-> 已归档文件到: {target_path}")
                        count_moved += 1
                    except Exception as e:
                        self.logger.error(f"   归档文件失败: {e}")

        self.logger.info("--------------------------------")
        self.logger.info(f"处理完成！")
        self.logger.info(f"成功归档文件数: {count_moved}")
        self.logger.info(f"移除重复文件数: {count_duplicate}")
        self.logger.info("--------------------------------")

if __name__ == "__main__":
    # ================= 配置区域 =================
    # 获取当前脚本所在目录
    # base_dir = Path(__file__).parent
    base_dir = Path(r"D:\AHAHA\DCIM")
    
    # 设置输入输出文件夹 (请根据实际情况修改)
    # 源文件夹：存放待处理乱序图片
    source_folder = base_dir / "Input_Photos" 
    
    # 目标文件夹：存放整理好的图片 (按年月)
    target_folder = base_dir / "Organized_Photos"
    
    # 重复文件夹：存放多余的重复文件
    duplicate_folder = base_dir / "Duplicates"
    
    # ================= 执行区域 =================
    print("正在初始化工具...")
    
    # 如果源文件夹不存在，创建它并提示用户
    if not source_folder.exists():
        source_folder.mkdir()
        print(f"提示：源文件夹 '{source_folder}' 不存在，已自动创建。")
        print("请将需要处理的图片放入该文件夹，然后再次运行此脚本。")
    else:
        # 实例化工具并运行
        # 注意：这里会自动查找当前目录下的 exiftool/exiftool.exe
        tool = ImageTool(source_folder, target_folder, duplicate_folder)
        tool.process_files()
