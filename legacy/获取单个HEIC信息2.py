# coding:utf-8
import time
import traceback
from pathlib import Path
from exiftool import ExifTool

# exiftool_path = r"C:\Users\Administrator\Desktop\exif\exiftool.exe"
# exiftool_path = r"C:\Users\Administrator\Desktop\exiftool\exiftool.exe"
exiftool_path = r"C:\Users\Administrator\Desktop\exiftool\exiftool.exe"
# file_path = r"C:\Users\Administrator\Desktop\exif\IMG_0088.HEIC"
# file_path = r"C:\Users\Administrator\Desktop\exif\jiajing.PNG"
# file_path = r"C:\Users\Administrator\Desktop\exif\image\IMG_0214.TIF"
# source_dir = r"C:\Users\Administrator\Desktop\exiftool\image"
# source_dir = r"C:\Users\Administrator\Desktop\exif\image\IMG_0089.HEIC"
# file_path = r"C:\Users\Administrator\Desktop\exiftool\Year\NOTIME\IMG_1730_1.JPG"
file_path = r"C:\Users\Administrator\Desktop\exiftool\Year\NOTIME\IMG_5308 （已编辑）.PNG"

print(file_path)

# print(str(file_path))

try:
	with ExifTool(executable=exiftool_path, encoding="utf-8") as et:
		# print("ExifTool started successfully")
		metadata = et.execute_json(str(file_path), "-j")[0]
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

except Exception:
	print(f"Error: {traceback.format_exc()}")
