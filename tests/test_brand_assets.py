import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_local_brand_assets_are_release_quality() -> None:
    brand = ROOT / "custom_components" / "resiyhome_health_sync" / "brand"
    icon_width, icon_height = _png_size(brand / "icon.png")
    logo_width, logo_height = _png_size(brand / "logo.png")
    assert icon_width == icon_height
    assert icon_width >= 256
    assert logo_width > logo_height
    assert logo_width >= 1024


def test_readme_artwork_exists() -> None:
    width, height = _png_size(ROOT / "assets" / "health-sync-by-resiyhome.png")
    assert width > height
