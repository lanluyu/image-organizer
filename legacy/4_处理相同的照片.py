# coding:utf-8
import os
import shutil
from PIL import Image
import numpy as np
from pathlib import Path
from hashlib import md5


class DuplicateImageFinder:
	def __init__(self, source_dir, dest_dir):
		"""初始化工具类，设置源文件夹和目标文件夹"""
		self.source_dir = Path(source_dir)
		self.dest_dir = Path(dest_dir)
		self.processed_images = []  # 存储已经处理过的图像的哈希值
		self.duplicate_images = []  # 存储重复图片的路径
		
		# 如果目标文件夹不存在，则创建
		if not self.dest_dir.exists():
			self.dest_dir.mkdir(parents=True)
	
	def hash_image(self, image_path):
		"""生成图片的哈希值，用于快速比较图片内容"""
		try:
			img = Image.open(image_path)
			img = img.convert("RGB")  # 确保图片是 RGB 格式
			img_data = np.array(img)
			# 使用 MD5 哈希值来代表图片的像素数据
			return md5(img_data.tobytes()).hexdigest()
		except Exception as e:
			print(f"无法处理图片 {image_path}: {e}")
			return None
	
	def find_duplicates(self):
		"""遍历文件夹并查找重复图片"""
		for root, dirs, files in os.walk(self.source_dir):
			for file in files:
				file_path = Path(root) / file
				
				# 如果是图片文件（根据后缀名筛选）
				if file_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov', '.avi', '.heic', '.gif', '.dng', '.tiff', '.tif', '.jfif', '.ico']:
					print(f"正在处理图片: {file_path}")
					image_hash = self.hash_image(file_path)
					
					if image_hash:
						# 如果这张图片的哈希值已经存在，说明是重复的
						if image_hash in self.processed_images:
							self.duplicate_images.append(file_path)
							# 将重复的图片剪切到新的文件夹中
							self.move_to_dest(file_path)
						else:
							# 否则添加哈希值到已处理的列表
							self.processed_images.append(image_hash)
	
	def move_to_dest(self, file_path):
		"""将重复的图片移动到目标文件夹"""
		try:
			dest_path = self.dest_dir / file_path.name
			# 如果目标文件夹已经存在同名文件，添加一个后缀
			counter = 1
			while dest_path.exists():
				dest_path = self.dest_dir / f"{file_path.stem}_{counter}{file_path.suffix}"
				counter += 1
			
			# 移动文件
			shutil.move(str(file_path), str(dest_path))
			print(f"已将重复图片移动到: {dest_path}")
		except Exception as e:
			print(f"移动图片 {file_path} 时出错: {e}")

# 示例使用1
source_folder = r'I:\NNN\NOTIMEE'
destination_folder = r'I:\NNN\Copy'

finder = DuplicateImageFinder(source_folder, destination_folder)
finder.find_duplicates()

