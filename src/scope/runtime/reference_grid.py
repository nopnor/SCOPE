from __future__ import annotations

from math import ceil, sqrt
from pathlib import Path

from PIL import Image, ImageOps


MAX_DIRECT_REFERENCE_IMAGES = 3
GRID_CELL_SIZE = 512
GRID_PADDING = 16
GRID_BACKGROUND = (255, 255, 255)


def collapse_reference_images_for_generation(
    reference_images: list[str],
    *,
    output_dir: str | Path,
    grid_stem: str = "reference_grid",
) -> tuple[list[str], str]:
    normalized = _existing_unique_paths(reference_images)
    if len(normalized) <= MAX_DIRECT_REFERENCE_IMAGES:
        return normalized, ""

    output_path = Path(output_dir) / f"{grid_stem}.jpg"
    grid_path = build_reference_grid(normalized, output_path=output_path)
    return [grid_path], grid_path


def build_reference_grid(image_paths: list[str], *, output_path: str | Path) -> str:
    valid_paths = _existing_unique_paths(image_paths)
    if not valid_paths:
        return ""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    tiles = [_build_tile(path) for path in valid_paths]
    columns = max(2, ceil(sqrt(len(tiles))))
    rows = ceil(len(tiles) / columns)
    canvas_width = columns * GRID_CELL_SIZE + (columns + 1) * GRID_PADDING
    canvas_height = rows * GRID_CELL_SIZE + (rows + 1) * GRID_PADDING
    canvas = Image.new("RGB", (canvas_width, canvas_height), GRID_BACKGROUND)

    for index, tile in enumerate(tiles):
        row = index // columns
        col = index % columns
        x = GRID_PADDING + col * (GRID_CELL_SIZE + GRID_PADDING)
        y = GRID_PADDING + row * (GRID_CELL_SIZE + GRID_PADDING)
        canvas.paste(tile, (x, y))

    canvas.save(output, format="JPEG", quality=95)
    return str(output)


def _build_tile(image_path: str) -> Image.Image:
    with Image.open(image_path) as image:
        tile = Image.new("RGB", (GRID_CELL_SIZE, GRID_CELL_SIZE), GRID_BACKGROUND)
        fitted = ImageOps.contain(image.convert("RGB"), (GRID_CELL_SIZE, GRID_CELL_SIZE))
        x = (GRID_CELL_SIZE - fitted.width) // 2
        y = (GRID_CELL_SIZE - fitted.height) // 2
        tile.paste(fitted, (x, y))
        return tile


def _existing_unique_paths(paths: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = str(raw_path).strip()
        key = path.casefold()
        if not path or key in seen:
            continue
        candidate = Path(path)
        if not candidate.is_file():
            continue
        unique.append(path)
        seen.add(key)
    return unique
