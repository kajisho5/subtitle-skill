from .srt import generate_srt
from .vtt import generate_vtt

SUPPORTED_FORMATS = ("srt", "vtt")

GENERATORS = {
    "srt": generate_srt,
    "vtt": generate_vtt,
}

__all__ = ["generate_srt", "generate_vtt", "SUPPORTED_FORMATS", "GENERATORS"]
