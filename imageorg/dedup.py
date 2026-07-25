# coding: utf-8
"""字节哈希、感知哈希与 BK-tree 近邻索引。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from .constants import IMAGE_EXTENSIONS
from .imaging import Image, imagehash


def md5_of(path: Path, block_size: int = 1024 * 1024) -> str:
    """计算文件 MD5 (分块读取避免大文件爆内存)"""
    h = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            h.update(block)
    return h.hexdigest()


def phash_of(path: Path) -> Optional[str]:
    """
    感知哈希 (pHash): 同一张照片不同压缩/分辨率会得到接近的哈希。
    返回 None 表示当前文件不适用 (非图片/Pillow 打不开)。
    """
    if imagehash is None or Image is None:
        return None
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return None
    try:
        with Image.open(path) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


def hamming(a: str, b: str) -> int:
    """两个十六进制 pHash 字符串的汉明距离"""
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 999  # 解析失败视为完全不同


class PHashBKTree:
    """
    汉明距离度量空间的 BK-tree，用于 pHash 视觉去重的近邻搜索。

    旧实现是 O(N²) 线性扫描 (每张新图 hamming 比对所有历史)，1 万张照片需要 ~5000 万次。
    BK-tree 利用三角不等式 |d(x,p) - d(y,p)| ≤ d(x,y) ≤ d(x,p) + d(y,p) 剪枝，
    实际查询接近 O(log N)，对小阈值 (≤4) 加速尤其明显。

    每个节点是 [hash_int, value, children_dict]，children 按"到父节点的距离"分桶。
    用 list 而非 tuple 是为了原地变更 children，避免重建。
    """

    def __init__(self) -> None:
        self.root: Optional[list] = None
        self._size = 0

    def __len__(self) -> int:
        return self._size

    @staticmethod
    def _dist(a: int, b: int) -> int:
        return bin(a ^ b).count("1")

    def add(self, hash_int: int, value) -> None:
        self._size += 1
        if self.root is None:
            self.root = [hash_int, value, {}]
            return
        node = self.root
        while True:
            d = self._dist(hash_int, node[0])
            child = node[2].get(d)
            if child is None:
                node[2][d] = [hash_int, value, {}]
                return
            node = child

    def query(self, hash_int: int, max_dist: int) -> list[tuple[int, object]]:
        """返回所有距离 ≤ max_dist 的 (距离, value)，按距离升序"""
        if self.root is None:
            return []
        results: list[tuple[int, object]] = []
        stack = [self.root]
        while stack:
            node = stack.pop()
            d = self._dist(hash_int, node[0])
            if d <= max_dist:
                results.append((d, node[1]))
            # 三角不等式: 子节点到 node 的距离若不在 [d-max_dist, d+max_dist]，
            # 则不可能到 hash_int 距离 ≤ max_dist，整子树跳过
            lo, hi = d - max_dist, d + max_dist
            for child_d, child in node[2].items():
                if lo <= child_d <= hi:
                    stack.append(child)
        results.sort(key=lambda x: x[0])
        return results
