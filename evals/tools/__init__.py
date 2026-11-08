"""Offline tooling for the eval harness (E0). No network, no credentials.

ONE deliberate exception: `evals/tools/collect_attrs.py` (E3-T3) is the one-shot, explicitly
invoked collector that makes 40 real VLM calls and writes a frozen predictions artifact. It is
never imported by `evals.run`, `pytest` or `make test`; those stay strictly zero-network.
"""
