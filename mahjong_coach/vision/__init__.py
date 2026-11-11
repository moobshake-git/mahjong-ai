"""Camera input for the coach.

A tile detector (a trained model, a vision LLM, or the built-in simulator)
turns each camera frame into a list of detections. ``tracker.Tracker`` turns
that stream into game events (hand set, I drew, someone threw, ...) and the
same table state the manual page builds, so the engine and Jev work unchanged.

    detections -> Tracker.feed(frame) -> events + table state -> /api/advise

Standard library only, except ``photo.py`` (optional ``anthropic`` package).
"""
from .tracker import Tracker

__all__ = ["Tracker"]
