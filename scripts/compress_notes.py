#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

import fitz
from PIL import Image


MAX_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class Profile:
    dpi: int
    quality: int


PROFILES = (
    Profile(dpi=144, quality=75),
    Profile(dpi=120, quality=68),
    Profile(dpi=100, quality=60),
    Profile(dpi=85, quality=52),
)


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}B"
        size /= 1024
    return f"{size:.1f}GB"


def validate_pdf(path: Path) -> int:
    with fitz.open(path) as doc:
        pages = doc.page_count
        if pages <= 0:
            raise RuntimeError(f"{path} has no pages")
        return pages


def render_pdf_to_images_pdf(
    source: Path,
    target: Path,
    profile: Profile,
    start_page: int = 0,
    end_page: int | None = None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    scale = profile.dpi / 72
    matrix = fitz.Matrix(scale, scale)

    with fitz.open(source) as src, fitz.open() as out:
        page_count = src.page_count
        if end_page is None:
            end_page = page_count

        for index in range(start_page, end_page):
            page = src.load_page(index)
            pix = page.get_pixmap(matrix=matrix, alpha=False, colorspace=fitz.csRGB)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

            jpeg_path = target.with_suffix(f".page-{index + 1}.jpg")
            image.save(
                jpeg_path,
                "JPEG",
                quality=profile.quality,
                optimize=True,
                progressive=True,
            )

            rect = fitz.Rect(0, 0, page.rect.width, page.rect.height)
            out_page = out.new_page(width=page.rect.width, height=page.rect.height)
            out_page.insert_image(rect, filename=jpeg_path)
            jpeg_path.unlink()

        out.save(target, garbage=4, deflate=True, clean=True)

    validate_pdf(target)


def try_single_file(source: Path, target: Path) -> tuple[bool, Profile | None]:
    for profile in PROFILES:
        render_pdf_to_images_pdf(source, target, profile)
        size = target.stat().st_size
        print(f"{source.name}: {profile.dpi}dpi q{profile.quality} -> {human_size(size)}")
        if size <= MAX_BYTES:
            return True, profile
    return False, None


def split_and_compress(source: Path, target_stem: Path) -> list[Path]:
    with fitz.open(source) as doc:
        page_count = doc.page_count

    profile = PROFILES[0]
    parts = 2
    while parts <= page_count:
        produced: list[Path] = []
        pages_per_part = math.ceil(page_count / parts)
        ok = True

        for part_index in range(parts):
            start = part_index * pages_per_part
            end = min(start + pages_per_part, page_count)
            if start >= end:
                break

            target = target_stem.with_name(f"{target_stem.stem}_part{part_index + 1}.pdf")
            render_pdf_to_images_pdf(source, target, profile, start, end)
            produced.append(target)
            size = target.stat().st_size
            print(
                f"{source.name}: part {part_index + 1}/{parts} "
                f"pages {start + 1}-{end} -> {human_size(size)}"
            )
            if size > MAX_BYTES:
                ok = False

        if ok:
            return produced

        for path in produced:
            path.unlink(missing_ok=True)
        parts += 1

    raise RuntimeError(f"Could not split {source} into files below {human_size(MAX_BYTES)}")


def compress_note(source: Path, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / source.name

    if source.stat().st_size <= MAX_BYTES:
        shutil.copy2(source, target)
        validate_pdf(target)
        return [target]

    ok, _ = try_single_file(source, target)
    if ok:
        return [target]

    target.unlink(missing_ok=True)
    return split_and_compress(source, output_dir / source.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("notes"))
    args = parser.parse_args()

    outputs: list[Path] = []
    for pdf in args.pdfs:
        outputs.extend(compress_note(pdf, args.output_dir))

    too_large = [path for path in outputs if path.stat().st_size > MAX_BYTES]
    if too_large:
        for path in too_large:
            print(f"too large: {path} {human_size(path.stat().st_size)}")
        raise SystemExit(1)

    print("\nFinal outputs:")
    for path in outputs:
        pages = validate_pdf(path)
        print(f"{path}: {pages} pages, {human_size(path.stat().st_size)}")


if __name__ == "__main__":
    main()
