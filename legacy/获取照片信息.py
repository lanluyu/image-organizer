# coding:utf-8
import exifread
import shutil
import traceback
from PIL import Image
from datetime import datetime

# file_path = r'I:\New\iPhone\2025\20180818_065244.DNG'
# file_path = r'I:\New\iPhone\2019\20190502_145151.JPG'
file_path = r'G:\New\iPhone\NOTIME\IMG_4526.JPG'

image = Image.open(file_path)
exif_data = image._getexif()
if exif_data:
	print(exif_data)
	creation_time = exif_data.get(36867)
	if creation_time:
		print(datetime.strptime(creation_time, '%Y:%m:%d %H:%M:%S').year)

# 打开图像文件
with open(file_path, 'rb') as f:
	tags = exifread.process_file(f)

print(tags)

# 打印所有 EXIF 标签
for tag in tags.keys():
	if 'DateTime' in tag:
		value = tags.get(tag)
		print(f"原始字段: {tag}: {tags[tag]}")
		image_time = str(value)
		print(f'照片时间: {image_time}')
		print(datetime.strptime(image_time, '%Y:%m:%d %H:%M:%S').year)