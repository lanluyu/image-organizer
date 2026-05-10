# coding:utf-8
import os
import shutil
from collections import defaultdict

class VideoFileOrganizer:
	def __init__(self, source_folder, folder_a, folder_b):
		self.source_folder = source_folder
		self.folder_a = folder_a
		self.folder_b = folder_b
		self.file_sizes = defaultdict(list)
		
		# 确保文件夹A和B存在
		os.makedirs(folder_a, exist_ok=True)
		os.makedirs(folder_b, exist_ok=True)
	
	def get_video_files(self):
		"""遍历文件夹及其子文件夹，返回所有视频文件及其路径"""
		video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv', '.webm'}
		video_files = []
		
		for root, _, files in os.walk(self.source_folder):
			for file in files:
				if any(file.lower().endswith(ext) for ext in video_extensions):
					video_files.append(os.path.join(root, file))
		
		return video_files
	
	def organize_files(self):
		"""根据文件大小将视频文件复制到文件夹A或B"""
		video_files = self.get_video_files()
		
		for file_path in video_files:
			file_size = os.path.getsize(file_path)
			file_name = os.path.basename(file_path)
			
			# 判断文件是否已存在于文件夹A或B中
			if file_size in self.file_sizes:
				file_exists_in_a = any(
					os.path.exists(os.path.join(self.folder_a, name)) for name in self.file_sizes[file_size]
				)
				
				if file_exists_in_a:
					# 如果已存在相同大小的文件，复制到文件夹B
					shutil.move(file_path, os.path.join(self.folder_b, file_name))
					print(f"文件 '{file_name}' 被复制到文件夹 B。")
				else:
					# 如果没有相同文件，复制到文件夹A
					shutil.move(file_path, os.path.join(self.folder_a, file_name))
					print(f"文件 '{file_name}' 被复制到文件夹 A。")
			else:
				# 如果是新文件大小，复制到文件夹A
				shutil.move(file_path, os.path.join(self.folder_a, file_name))
				print(f"文件 '{file_name}' 被复制到文件夹 A。")
			
			# 记录文件大小和文件名
			self.file_sizes[file_size].append(file_name)


if __name__ == '__main__':
	source_folder = r'C:\Users\Administrator\Desktop\NNN'  # 源文件夹路径
	folder_a = r'C:\Users\Administrator\Desktop\NNN\MOVA'  # 文件夹A路径
	folder_b = r'C:\Users\Administrator\Desktop\NNN\MOVB'  # 文件夹B路径
	
	organizer = VideoFileOrganizer(source_folder, folder_a, folder_b)
	organizer.organize_files()
