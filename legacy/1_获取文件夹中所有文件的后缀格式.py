# coding:utf-8
import time
import json
import requests
from pathlib import Path

def get_file_extensions(directory):
	"""获取文件夹中所有文件的后缀格式"""
	extensions = set()  # 使用集合避免重复
	for file_path in Path(directory).rglob('*'):  # rglob 遍历所有文件
		if file_path.is_file():
			ext = file_path.suffix.lower()  # 获取文件扩展名并转换为小写
			if ext:  # 排除没有扩展名的文件
				extensions.add(ext)
	return extensions

# 示例使用
directory = r'I:\NNN\NOTIMEE'
extensions = get_file_extensions(directory)
print(f"文件夹中的后缀格式：{extensions}")

# {'.heic', '.png', '.aae', '.mp4', '.jpeg', '.jpg', '.mov'}
# {'.jpg', '.png', '.mov', '.jpeg', '.gif', '.mp4', '.heic', '.dng', '.aae'}
# {'.mp4', '.jpg', '.mov', '.gif', '.jpeg', '.aae', '.dng', '.heic', '.png'}
# {'.tiff', '.jpeg', '.tif', '.mov', '.gif', '.heic', '.dng', '.png', '.mp4', '.jpg'}