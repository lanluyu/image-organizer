# coding:utf-8
import time
import json
import requests

import xml.etree.ElementTree as ET

def extract_date_from_aae(aae_file):
	"""从 .aae 文件中提取 <date> 字段"""
	try:
		tree = ET.parse(aae_file)
		root = tree.getroot()
		
		# 查找 <date> 元素
		date_element = root.find('.//date')
		if date_element is not None:
			return date_element.text
		else:
			print("未找到 <date> 字段")
			return None
	except Exception as e:
		print(f"读取 .aae 文件时出错: {e}")
		return None

# 示例用法
aae_file = r'C:\Users\Administrator\Desktop\image\新建文件夹\IMG_8363.AAE'
date = extract_date_from_aae(aae_file)
if date:
	print(f"提取的时间戳：{date.replace('T', ' ').replace('Z', '')}")
else:
	print("没有找到时间戳")

