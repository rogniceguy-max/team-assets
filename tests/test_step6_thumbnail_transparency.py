from pathlib import Path
import pytest
from PIL import Image
from media_utils import inspect_media

def test_transparent_png_thumbnail_has_white_background(tmp_path):
    img_path = tmp_path / "transparent_logo.png"
    thumb_path = tmp_path / "thumb.jpg"

    # 100x100 RGBA image: fully transparent (0,0,0,0) with a 20x20 solid red center
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(40, 60):
        for y in range(40, 60):
            im.putpixel((x, y), (255, 0, 0, 255))
    im.save(img_path, "PNG")

    res = inspect_media(img_path, "transparent_logo.png", "image/png", thumb_path)
    assert res["kind"] == "image"
    assert res["width"] == 100
    assert res["height"] == 100
    assert thumb_path.exists()

    with Image.open(thumb_path) as thumb:
        # Check corner (0, 0): in old code without compositing, this was (0, 0, 0) pure black!
        # With white compositing, it must be white (allowing small JPEG compression delta >= 240)
        corner_r, corner_g, corner_b = thumb.getpixel((0, 0))
        assert corner_r >= 240 and corner_g >= 240 and corner_b >= 240, f"Expected white corner, got {(corner_r, corner_g, corner_b)}"

        # Check center (50, 50): should be red
        center_r, center_g, center_b = thumb.getpixel((50, 50))
        assert center_r >= 200 and center_g <= 30 and center_b <= 30, f"Expected red center, got {(center_r, center_g, center_b)}"

def test_opaque_image_thumbnail_unchanged(tmp_path):
    img_path = tmp_path / "opaque.png"
    thumb_path = tmp_path / "thumb_opaque.jpg"

    im = Image.new("RGB", (80, 80), (0, 128, 255))
    im.save(img_path, "PNG")

    res = inspect_media(img_path, "opaque.png", "image/png", thumb_path)
    assert res["kind"] == "image"

    with Image.open(thumb_path) as thumb:
        r, g, b = thumb.getpixel((10, 10))
        assert r <= 20 and 110 <= g <= 145 and 235 <= b <= 255
