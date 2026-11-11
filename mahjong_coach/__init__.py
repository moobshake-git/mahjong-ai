"""Mahjong turn coach: in-the-moment help deciding what to discard or call."""
from .advisor import TableState, advise
from .scoring import Meld, Rules, WinContext, score_win
from .tiles import parse_tile, parse_tiles, tile_label, tile_name

__all__ = ["TableState", "advise", "Meld", "Rules", "WinContext", "score_win",
           "parse_tile", "parse_tiles", "tile_label", "tile_name"]
__version__ = "0.1.0"
