from .srt import generate_srt, parse_srt
from .ttml import generate_ebu_tt_d, generate_imsc, parse_ttml, validate_ttml
from .vtt import generate_vtt

#: `ttml` is IMSC 1.1 Text Profile; `ebu-tt-d` is EBU Tech 3380 (docs/adr/ADR-007-ttml-output.md).
SUPPORTED_FORMATS = ("srt", "vtt", "ttml", "ebu-tt-d")

#: formats whose written document is checked against its profile before it is reported
TTML_FORMATS = ("ttml", "ebu-tt-d")

GENERATORS = {
    "srt": generate_srt,
    "vtt": generate_vtt,
    "ttml": generate_imsc,
    "ebu-tt-d": generate_ebu_tt_d,
}

__all__ = [
    "generate_srt",
    "generate_vtt",
    "generate_imsc",
    "generate_ebu_tt_d",
    "parse_srt",
    "parse_ttml",
    "validate_ttml",
    "SUPPORTED_FORMATS",
    "TTML_FORMATS",
    "GENERATORS",
]
