# coding:utf-8
import os
import time
import shutil
from PIL import Image
from pathlib import Path
from datetime import datetime
import xml.etree.ElementTree as ET


class FileOrganizer:
	def __init__(self, source_dir, target_dir):
		self.source_dir = Path(source_dir)
		self.target_dir = Path(target_dir)
		
		if not self.target_dir.exists():
			self.target_dir.mkdir(parents=True)
	
	def get_creation_year(self, file_path):
		"""根据文件的创建时间返回年份"""
		if file_path.suffix.lower() in ['.aae']:
		# if file_path.suffix.lower() in ['.gif']:
			try:
				tree = ET.parse(file_path)
				root = tree.getroot()
				# 查找 <date> 元素
				date_element = root.find('.//date')
				if date_element is not None:
					datetime_str = date_element.text.replace('T', ' ').replace('Z', '')
					return datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S').year
				else:
					print("未找到 <date> 字段")
			except Exception as e:
				print(f"Error reading creation time for {file_path}: {e}")
		
		# Fallback to filesystem's creation time (Windows-specific)
		return datetime.fromtimestamp(file_path.stat().st_ctime).year
	
	def get_unique_filename(self, target_path):
		"""检查目标文件夹中是否已存在文件，如果存在则修改文件名"""
		original_path = target_path
		counter = 1
		while target_path.exists():
			target_path = original_path.with_name(f"{original_path.stem}_{counter}{original_path.suffix}")
			counter += 1
		return target_path
	
	def copy_files(self):
		"""遍历源文件夹，复制文件到目标文件夹并按年份分类"""
		for root, dirs, files in os.walk(self.source_dir):
			for file in files:
				file_path = Path(root) / file
				if file_path.suffix.lower() in ['.aae']:
				# if file_path.suffix.lower() in ['.gif']:
					# 获取文件的创建年份
					year = self.get_creation_year(file_path)
					
					# 在目标文件夹下创建年份文件夹
					year_folder = self.target_dir / str(year)
					if not year_folder.exists():
						year_folder.mkdir()
					
					# 复制文件到对应的年份文件夹，并确保文件名唯一
					target_path = year_folder / file
					unique_target_path = self.get_unique_filename(target_path)
					
					try:
						shutil.move(file_path, unique_target_path)
						print(f"文件已移动: {file_path} -> {unique_target_path}")
					except Exception as e:
						print(f"移动文件时出错 {file_path}: {e}")

# 使用示例
# 把存在的 AAE 文件转移到别的文件夹中, 最好同级目录
source_directory = r'I:\NNN\NOTIMEE'
target_directory = r'I:\NNN\AAE'
# target_directory = r'G:\New\gif'

organizer = FileOrganizer(source_directory, target_directory)
organizer.copy_files()

