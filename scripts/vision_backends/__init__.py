#!/usr/bin/env python3
"""Stub vision backend for scripts/annotate_clip.py.

Returns a fixed FAKE verdict. Useful for testing the annotation flow
without paying for a real vision API call. Replace with a real backend
by setting VISION_BACKEND to point at it.
"""
def analyse(image_url: str, prompt: str) -> str:
    return "FAKE: stub backend — not configured"
