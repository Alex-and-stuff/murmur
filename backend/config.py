"""Shared constants for the Murmur backend."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "dist"
UPLOADS_DIR = STATIC_DIR / "uploads"

SAMPLE_RATE = 16_000
MAX_AUDIO_BYTES = SAMPLE_RATE * 4 * 30
MAX_FETCH_BODY_BYTES = 4_096
MAX_SUMMARY_BODY_BYTES = 256_000
MAX_SUMMARY_TRANSCRIPT_CHARS = 24_000

DEFAULT_ASR_MODEL = "mlx-community/Qwen3-ASR-1.7B-8bit"
DEFAULT_SUMMARY_MODEL = "Qwen/Qwen3-8B-MLX-4bit"
