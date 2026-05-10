# coding:utf-8
import time
import json
import requests
import os
import time
from PIL import Image
from PIL.ExifTags import TAGS
import exifread
import piexif


def get_one(file_path):
	# 获取文件的创建时间
	creation_time = os.path.getctime(file_path)
	print("文件创建时间:", time.ctime(creation_time))
	
	
def get_two(file_path):
	image = Image.open(file_path)
	exif_data = image._getexif()  # 获取图片的EXIF数据
	if not exif_data:
		return None
	
	# 将 EXIF 数据转化为字典
	exif_dict = {}
	for tag, value in exif_data.items():
		tag_name = TAGS.get(tag, tag)
		exif_dict[tag_name] = value
		
	for tag, value in exif_data.items():
		print(f"{tag}: {value}")
	else:
		print("没有找到EXIF数据")



def get_exif_data_with_exifread(file_path):
	with open(file_path, 'rb') as f:
		tags = exifread.process_file(f)
		
	exif_data = tags

	# 获取拍摄时间和设备信息
	creation_time = exif_data.get('EXIF DateTimeOriginal')
	camera_model = exif_data.get('Image Model')
	camera_make = exif_data.get('Image Make')
	
	print("拍摄时间:", creation_time)
	print("拍摄设备:", camera_make, camera_model)


def get_exif_data_piexif(file_path):
	exif_dict = piexif.load(file_path)

	exif_data = exif_dict
	
	# 获取拍摄时间和设备信息
	creation_time = exif_data['0th'].get(piexif.ImageIFD.DateTime).decode('utf-8')
	camera_model = exif_data['0th'].get(piexif.ImageIFD.Model).decode('utf-8')
	camera_make = exif_data['0th'].get(piexif.ImageIFD.Make).decode('utf-8')
	
	print("拍摄时间:", creation_time)
	print("拍摄设备:", camera_make, camera_model)


file_path = r'I:\2025_New\iPhone\NOTIME\IMG_1180.dng'
get_one(file_path)
# get_two(file_path)
get_exif_data_with_exifread(file_path)
get_exif_data_piexif(file_path)