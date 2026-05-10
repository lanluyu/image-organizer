# coding:utf-8
import os
import shutil
import time
import piexif
import exifread
from pathlib import Path
from PIL import Image
from datetime import datetime
from exiftool import ExifTool


class FileOrganizer:
	def __init__(self, source_dir, target_dir):
		self.source_dir = Path(source_dir)
		self.target_dir = Path(target_dir)
		
		if not self.target_dir.exists():
			self.target_dir.mkdir(parents=True)
	
	def get_creation_year(self, file_path):
		"""根据文件的创建时间返回年份"""
		year_str = None
		
		if file_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov', '.gif', '.dng', '.avi', '.heic', '.tiff', '.tif']:
			try:
				if file_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.heic', '.gif', '.dng', '.tiff', '.tif']:
					try:
						image = Image.open(file_path)
						exif_data = image._getexif()
						if exif_data:
							print(exif_data)
							creation_time = exif_data.get(36867)
							if creation_time:
								year_str = datetime.strptime(creation_time, '%Y:%m:%d %H:%M:%S').year
					except Exception as e:
						print(f"Error reading creation time for {file_path}: {e}")
					
					if not year_str:
						try:
							# 打开图像文件
							with open(file_path, 'rb') as f:
								tags = exifread.process_file(f)
							# 打印所有 EXIF 标签
							for tag in tags.keys():
								if 'DateTime' in tag:
									value = tags.get(tag)
									image_time = str(value)
									if image_time:
										year_str = datetime.strptime(image_time, '%Y:%m:%d %H:%M:%S').year
							
							if not year_str:
								exif_data = tags
								# 获取拍摄时间和设备信息
								creation_time = exif_data.get('EXIF DateTimeOriginal')
								camera_model = exif_data.get('Image Model')
								camera_make = exif_data.get('Image Make')
								
								# print("拍摄时间:", creation_time)
								# print("拍摄设备:", camera_make, camera_model)
								
								if creation_time:
									year_str = datetime.strptime(creation_time, '%Y:%m:%d %H:%M:%S').year
									
						except Exception as e:
							print(f"Error reading creation time for {file_path}: {e}")
					
					if not year_str:
						try:
							exif_dict = piexif.load(file_path)
							
							exif_data = exif_dict
							
							# 获取拍摄时间和设备信息
							creation_time = exif_data['0th'].get(piexif.ImageIFD.DateTime).decode('utf-8')
							camera_model = exif_data['0th'].get(piexif.ImageIFD.Model).decode('utf-8')
							camera_make = exif_data['0th'].get(piexif.ImageIFD.Make).decode('utf-8')
							
							# print("拍摄时间:", creation_time)
							# print("拍摄设备:", camera_make, camera_model)
							if creation_time:
								year_str = datetime.strptime(creation_time, '%Y:%m:%d %H:%M:%S').year
								
						except Exception as e:
							print(f"Error reading creation time for {file_path}: {e}")
			except Exception as e:
				print(f"Error reading creation time for {file_path}: {e}")
		
		# Fallback to filesystem's creation time (Windows-specific)
		# return datetime.fromtimestamp(file_path.stat().st_ctime).year
		
		if year_str:
			return year_str
		else:
			return 'NOTIME'
	
	def get_creation_year2(self, file_path):
		"""根据文件的创建时间返回年份"""
		year_str = None
		
		if file_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov', '.gif', '.dng', '.avi', '.heic', '.tiff', '.tif', '.jfif', '.ico']:
			try:
				if file_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.heic', '.gif', '.dng', '.tiff', '.tif', '.jfif', '.ico']:
					try:
						exiftool_path = r"C:\Users\Administrator\Desktop\exiftool\exiftool.exe"
						with ExifTool(executable=exiftool_path, encoding="utf-8") as et:
							# print("ExifTool started successfully")
							metadata = et.execute_json(str(file_path))[0]
							# print(metadata)
							Image_date = None
							if "EXIF:DateTimeOriginal" in metadata:
								Image_date = metadata.get("EXIF:DateTimeOriginal")
							
							if not Image_date:
								Image_date = metadata.get("EXIF:CreateDate")
							
							if not Image_date:
								Image_date = metadata.get("XMP:CreateDate")
							
							if not Image_date:
								Image_date = metadata.get("XMP:ModifyDate")
							
							if not Image_date:
								Image_date = metadata.get("File:FileModifyDate")
							
							print(f'拍摄时间: {Image_date}')
							
							if Image_date:
								year_str = Image_date.split(':')[0]
					except Exception as e:
						print(f"Error: {e}")
			except Exception as e:
				print(f"Error reading creation time for {file_path}: {e}")
		
		# Fallback to filesystem's creation time (Windows-specific)
		# return datetime.fromtimestamp(file_path.stat().st_ctime).year
		
		if year_str:
			return year_str
		else:
			return 'NOTIME'
		
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
				if file_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.mp4', '.mov', '.avi', '.heic', '.gif', '.dng', '.tiff', '.tif', '.jfif', '.ico']:
					# 获取文件的创建年份
					# year = self.get_creation_year(file_path)
					year = self.get_creation_year2(file_path)
					
					# 在目标文件夹下创建年份文件夹
					year_folder = self.target_dir / str(year)
					if not year_folder.exists():
						year_folder.mkdir()
					
					# 复制文件到对应的年份文件夹，并确保文件名唯一
					target_path = year_folder / file
					unique_target_path = self.get_unique_filename(target_path)
					
					try:
						shutil.move(file_path, unique_target_path)
						print(f"文件已复制: {file_path} -> {unique_target_path}")
					except Exception as e:
						print(f"复制文件时出错 {file_path}: {e}")

# 使用示例
source_directory = r'I:\NNN\NOTIMEE'
target_directory = r'I:\NNN\Year'

organizer = FileOrganizer(source_directory, target_directory)
organizer.copy_files()

