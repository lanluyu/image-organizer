from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None


SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".heic", ".dng", ".tiff", ".tif", ".jfif", ".ico",
    ".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm", ".aae",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".dng", ".tiff", ".tif", ".jfif", ".ico"}

DATE_KEYS = [
    "EXIF:DateTimeOriginal",
    "EXIF:CreateDate",
    "QuickTime:CreateDate",
    "QuickTime:MediaCreateDate",
    "QuickTime:TrackCreateDate",
    "QuickTime:CreationDate",
    "XMP:CreateDate",
    "XMP:ModifyDate",
    "File:FileModifyDate",
    "DateTimeOriginal",
    "CreateDate",
    "CreationDate",
    "ModifyDate",
]


@dataclass
class OrganizerStats:
    scanned: int = 0
    moved: int = 0
    duplicates: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass
class OrganizerConfig:
    source_dir: Path
    target_dir: Path
    duplicate_dir: Path
    exiftool_path: Optional[Path] = None
    dry_run: bool = False
    block_size: int = 1024 * 1024
    stats: OrganizerStats = field(default_factory=OrganizerStats)


class MediaDateResolver:
    def __init__(self, exiftool_path: Optional[Path]) -> None:
        self.exiftool_path = exiftool_path if exiftool_path and exiftool_path.exists() else None

    def resolve(self, file_path: Path) -> datetime:
        if file_path.suffix.lower() == ".aae":
            dt = self._date_from_aae(file_path)
            if dt:
                return dt

        dt = self._date_from_exiftool(file_path)
        if dt:
            return dt

        dt = self._date_from_pillow(file_path)
        if dt:
            return dt

        return datetime.fromtimestamp(file_path.stat().st_mtime)

    def _date_from_aae(self, file_path: Path) -> Optional[datetime]:
        try:
            root = ET.parse(file_path).getroot()
            date_node = root.find(".//date")
            if date_node is None or not date_node.text:
                return None
            value = date_node.text.replace("T", " ").replace("Z", "")
            return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    def _date_from_exiftool(self, file_path: Path) -> Optional[datetime]:
        if not self.exiftool_path:
            return None

        cmd = [
            str(self.exiftool_path),
            "-j",
            "-G",
            "-charset",
            "filename=utf8",
            "-DateTimeOriginal",
            "-CreateDate",
            "-CreationDate",
            "-MediaCreateDate",
            "-TrackCreateDate",
            "-ContentCreateDate",
            "-ModifyDate",
            "-FileModifyDate",
            str(file_path),
        ]

        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                startupinfo=startupinfo,
                check=False,
            )
            if result.returncode != 0:
                return None

            records = json.loads(result.stdout)
            if not records:
                return None
            info = records[0]

            for key in DATE_KEYS:
                raw = info.get(key)
                if not raw:
                    continue
                try:
                    return datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    continue
        except Exception:
            return None

        return None

    def _date_from_pillow(self, file_path: Path) -> Optional[datetime]:
        if Image is None or file_path.suffix.lower() not in IMAGE_EXTENSIONS:
            return None

        try:
            with Image.open(file_path) as img:
                exif_data = getattr(img, "_getexif", lambda: None)()
            if not exif_data:
                return None
            raw = exif_data.get(36867) or exif_data.get(306)
            if not raw:
                return None
            return datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S")
        except Exception:
            return None


class MediaOrganizer:
    def __init__(self, config: OrganizerConfig) -> None:
        self.cfg = config
        self.logger = logging.getLogger("media_organizer")
        self.date_resolver = MediaDateResolver(config.exiftool_path)
        self._seen_size_to_hashes: dict[int, set[str]] = {}

    def run(self) -> OrganizerStats:
        self.cfg.target_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.duplicate_dir.mkdir(parents=True, exist_ok=True)

        for file_path in self._iter_files(self.cfg.source_dir):
            self.cfg.stats.scanned += 1
            suffix = file_path.suffix.lower()
            if suffix not in SUPPORTED_EXTENSIONS:
                self.cfg.stats.skipped += 1
                continue

            try:
                if self._is_duplicate(file_path):
                    self._move_duplicate(file_path)
                    self.cfg.stats.duplicates += 1
                    continue

                dt = self.date_resolver.resolve(file_path)
                target_dir = self.cfg.target_dir / f"{dt.year:04d}" / f"{dt.month:02d}"
                self._move_file(file_path, target_dir)
                self.cfg.stats.moved += 1
            except Exception as exc:
                self.cfg.stats.errors += 1
                self.logger.error("Failed for %s: %s", file_path, exc)

        return self.cfg.stats

    def _iter_files(self, source_dir: Path) -> Iterable[Path]:
        for root, _, files in os.walk(source_dir):
            for name in files:
                if name.startswith("."):
                    continue
                yield Path(root) / name

    def _is_duplicate(self, file_path: Path) -> bool:
        size = file_path.stat().st_size
        hash_bucket = self._seen_size_to_hashes.setdefault(size, set())

        file_hash = self._hash_file(file_path)
        if file_hash in hash_bucket:
            return True

        hash_bucket.add(file_hash)
        return False

    def _hash_file(self, file_path: Path) -> str:
        md5 = hashlib.md5()
        with file_path.open("rb") as f:
            for block in iter(lambda: f.read(self.cfg.block_size), b""):
                md5.update(block)
        return md5.hexdigest()

    def _move_duplicate(self, file_path: Path) -> None:
        target = self._unique_path(self.cfg.duplicate_dir, file_path.name)
        self._move(file_path, target)

    def _move_file(self, file_path: Path, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = self._unique_path(target_dir, file_path.name)
        self._move(file_path, target)

    def _move(self, source: Path, target: Path) -> None:
        self.logger.info("MOVE %s -> %s", source, target)
        if not self.cfg.dry_run:
            shutil.move(str(source), str(target))

    @staticmethod
    def _unique_path(directory: Path, filename: str) -> Path:
        path = directory / filename
        stem = path.stem
        suffix = path.suffix
        i = 1
        while path.exists():
            path = directory / f"{stem}_{i}{suffix}"
            i += 1
        return path


def detect_default_exiftool() -> Optional[Path]:
    local = Path(__file__).parent / "exiftool" / "exiftool.exe"
    return local if local.exists() else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Organize media files by date and move duplicates.")
    parser.add_argument("--source", required=True, type=Path, help="Source directory")
    parser.add_argument("--target", required=True, type=Path, help="Target directory")
    parser.add_argument("--duplicates", required=True, type=Path, help="Duplicate directory")
    parser.add_argument("--exiftool", type=Path, default=detect_default_exiftool(), help="Path to exiftool.exe")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not move files")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s | %(levelname)s | %(message)s")

    cfg = OrganizerConfig(
        source_dir=args.source,
        target_dir=args.target,
        duplicate_dir=args.duplicates,
        exiftool_path=args.exiftool,
        dry_run=args.dry_run,
    )

    organizer = MediaOrganizer(cfg)
    stats = organizer.run()

    logging.info(
        "Done. scanned=%s moved=%s duplicates=%s skipped=%s errors=%s",
        stats.scanned,
        stats.moved,
        stats.duplicates,
        stats.skipped,
        stats.errors,
    )
    return 0 if stats.errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
