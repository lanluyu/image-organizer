# coding:utf-8
import time
import json
from PIL import Image
import pillow_heif
from PIL.ExifTags import TAGS
import datetime
from exiftool import ExifToolHelper
import os
from pathlib import Path
from exiftool import ExifTool
# exiftool_path = r"C:\Users\Administrator\Desktop\exif\exiftool.exe"
exiftool_path = r"E:\OneDrive\文档\图片处理流程\exiftool\exiftool.exe"
et = ExifTool(executable=exiftool_path)

# 示例调用
# file_path = r"C:\Users\Administrator\Desktop\exif\image\梅奥.PNG"
file_path = r"D:\AHAHA\DCIM\Organized_Photos\2026\01\IMG_0003.JPG"
file_path = r"D:\AHAHA\DCIM\Organized_Photos\2026\01\IMG_0288.JPG"

try:
	with ExifTool(executable=exiftool_path) as et:
		# print("ExifTool started successfully")
		metadata = et.execute_json(str(file_path))[0]
		print(metadata)
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
		
		info = {
			"拍摄时间": metadata.get("EXIF:DateTimeOriginal"),
			"机型": metadata.get("EXIF:Model"),
			"图像宽度": metadata.get("File:ImageWidth"),
			"图像高度": metadata.get("File:ImageHeight"),
			"曝光时间": metadata.get("EXIF:ExposureTime"),
			"光圈值": metadata.get("EXIF:FNumber"),
			"ISO": metadata.get("EXIF:ISO"),
			"镜头型号": metadata.get("EXIF:LensModel")
		}
		
		print(info)

except Exception as e:
	print(f"Error: {e}")

