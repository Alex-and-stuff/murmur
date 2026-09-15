#!/usr/bin/env python3
"""Serve the Murmur UI and run chunked Qwen3-ASR inference locally."""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

if __package__ in (None, ""):  # allow `python backend/server.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.asr import BackendState
from backend.config import DEFAULT_ASR_MODEL, DEFAULT_SUMMARY_MODEL
from backend.http_app import create_server
from backend.summary import SummaryState


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument(
        "--backend",
        choices=("mlx", "fixture"),
        default=os.environ.get("MURMUR_ASR_BACKEND", "mlx"),
    )
    parser.add_argument(
        "--summary-backend",
        choices=("mlx", "fixture"),
        default=os.environ.get("MURMUR_SUMMARY_BACKEND", "mlx"),
    )
    parser.add_argument(
        "--summary-model",
        default=os.environ.get("MURMUR_SUMMARY_MODEL", DEFAULT_SUMMARY_MODEL),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MURMUR_ASR_MODEL", DEFAULT_ASR_MODEL),
    )
    args = parser.parse_args()

    state = BackendState()
    summary_state = SummaryState()

    def load_models() -> None:
        # Both MLX runtimes lazily import transformers. Initializing them in
        # parallel can expose a partially initialized lazy module, so keep
        # model startup off the HTTP thread but serialize the two loads.
        state.load(args.backend, args.model)
        summary_state.load(args.summary_backend, args.summary_model)

    threading.Thread(
        target=load_models,
        name="model-loader",
        daemon=True,
    ).start()
    server = create_server(args.host, args.port, state, summary_state=summary_state)
    print(f"Murmur is available at http://{args.host}:{server.server_port}")
    print("The ASR and summary models are loading in the background. Keep this window open.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Murmur.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
