"""``python -m mrf_honest`` -- the invocation ``action.yml`` and the docs use.

The console script installed from ``[project.scripts]`` needs the distribution to be installed.
The GitHub Action deliberately does not install anything: the package has no runtime dependencies
(ADR 0002, stdlib-only streaming core), so the action puts ``src`` on ``PYTHONPATH`` and runs the
checked-out source directly. That keeps the policy fingerprint a property of the pinned commit
rather than of whatever wheel happened to resolve, and it needs this module to exist.
"""

from __future__ import annotations

from mrf_honest.cli import main

if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess in tests
    raise SystemExit(main())
