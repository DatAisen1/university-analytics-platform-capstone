"""
scripts/verify_cmdstan.py

P0 Final Acceptance Test: the bug this closes. A fresh `pip install -r
requirements.txt` normally has prophet==1.1.5 vendor its own private copy
of CmdStan (compiled during pip install, via a network call to fetch the
CmdStan source) at:

    <site-packages>/prophet/stan_model/cmdstan-2.33.1/

If that network call is interrupted, rate-limited, or the build doesn't
fully complete, pip still reports success -- the directory exists, just
missing its Makefile/src/. Prophet's own backend loader only checks
`local_cmdstan.exists()`, not whether it's a *complete, working* install,
so the broken copy is silently preferred over a real one. The failure
this produces is misleading and far downstream of the real cause:

    AttributeError: 'Prophet' object has no attribute 'stan_backend'

raised from prophet/forecaster.py's `_load_stan_backend`, only visible at
`dagster job execute`'s Model Training stage -- 5 pipeline stages and
several minutes into a run, with no indication CmdStan is the problem.

This script surfaces and fixes that BEFORE any pipeline stage runs, by
actually fitting a real (tiny) Prophet model -- proving the backend
truly works, not just that a directory exists at some path.

Usage (after `pip install -r requirements.txt`, before `make bootstrap`
or `dagster job execute`):
    python3 scripts/verify_cmdstan.py
    python3 scripts/verify_cmdstan.py --reinstall   # force a clean reinstall

Exit code 0: Prophet can fit a model right now.
Exit code 1: it still can't, with the real underlying error printed --
             see the printed remediation steps (usually a missing C++
             toolchain, e.g. `apt-get install build-essential` /
             Xcode Command Line Tools on macOS).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _broken_vendored_cmdstan_dirs() -> list[Path]:
    """Prophet vendors CmdStan under its own package directory. Return any
    such directory that exists but is missing the files a real CmdStan
    install always has (Makefile, stan/ source tree) -- i.e. exactly the
    half-finished state this script exists to detect."""
    try:
        import importlib.resources as importlib_resources
    except ImportError:  # pragma: no cover - py<3.9 fallback, unused here
        import importlib_resources  # type: ignore

    stan_model_dir = Path(str(importlib_resources.files("prophet") / "stan_model"))
    if not stan_model_dir.is_dir():
        return []

    broken = []
    for candidate in stan_model_dir.glob("cmdstan-*"):
        if candidate.is_dir() and not (candidate / "Makefile").exists():
            broken.append(candidate)
    return broken


def _try_fit_prophet() -> tuple[bool, str]:
    """The only check that actually matters: construct AND fit a real
    Prophet model. Checking `cmdstanpy.cmdstan_path()` alone would have
    missed this exact bug -- the broken vendored path still "exists", it
    just doesn't work. Returns (ok, message)."""
    try:
        import numpy as np
        import pandas as pd
        from prophet import Prophet

        df = pd.DataFrame(
            {
                "ds": pd.date_range("2020-01-01", periods=12, freq="MS"),
                "y": np.linspace(10, 20, 12),
            }
        )
        model = Prophet()
        model.fit(df)
        model.predict(model.make_future_dataframe(periods=1, freq="MS"))
        return True, f"OK -- backend: {model.stan_backend.get_type()}"
    except Exception as exc:  # noqa: BLE001 - we want the real message either way
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reinstall",
        action="store_true",
        help="Remove any existing CmdStan install (vendored or cmdstanpy-managed) and reinstall from scratch, even if the current one looks fine.",
    )
    args = parser.parse_args()

    print("== Verifying Prophet/CmdStan backend ==")

    if args.reinstall:
        for broken in _broken_vendored_cmdstan_dirs():
            print(f"--reinstall: removing {broken}")
            shutil.rmtree(broken, ignore_errors=True)
        import cmdstanpy

        try:
            cmdstanpy.install_cmdstan(overwrite=True)
        except Exception as exc:  # noqa: BLE001
            print(f"cmdstanpy.install_cmdstan(overwrite=True) failed: {exc}", file=sys.stderr)
            return 1

    ok, message = _try_fit_prophet()
    if ok:
        print(f"PASS: {message}")
        return 0

    print(f"Initial check failed: {message}")

    broken_dirs = _broken_vendored_cmdstan_dirs()
    if broken_dirs:
        print(
            "Found incomplete vendored CmdStan install(s) -- this is the exact "
            "failure mode documented at the top of this script. Removing and "
            "reinstalling via cmdstanpy (not the fragile pip-install-time build):"
        )
        for broken in broken_dirs:
            print(f"  removing {broken}")
            shutil.rmtree(broken, ignore_errors=True)

        import cmdstanpy

        try:
            cmdstanpy.install_cmdstan()
        except Exception as exc:  # noqa: BLE001
            print(f"cmdstanpy.install_cmdstan() failed: {exc}", file=sys.stderr)
            _print_remediation()
            return 1

        ok, message = _try_fit_prophet()
        if ok:
            print(f"PASS after reinstall: {message}")
            return 0
        print(f"Still failing after reinstall: {message}", file=sys.stderr)
        _print_remediation()
        return 1

    # No broken vendored dir found, but Prophet still can't fit -- likely
    # no CmdStan installed at all yet, or a missing C++ toolchain.
    print("No broken vendored CmdStan directory found; attempting a fresh cmdstanpy install...")
    import cmdstanpy

    try:
        cmdstanpy.install_cmdstan()
    except Exception as exc:  # noqa: BLE001
        print(f"cmdstanpy.install_cmdstan() failed: {exc}", file=sys.stderr)
        _print_remediation()
        return 1

    ok, message = _try_fit_prophet()
    if ok:
        print(f"PASS after install: {message}")
        return 0

    print(f"Still failing: {message}", file=sys.stderr)
    _print_remediation()
    return 1


def _print_remediation() -> None:
    print(
        "\nRemediation:\n"
        "  1. Confirm a C++ toolchain is installed:\n"
        "       Debian/Ubuntu: apt-get install -y build-essential\n"
        "       macOS:         xcode-select --install\n"
        "  2. Re-run: python3 scripts/verify_cmdstan.py --reinstall\n"
        "  3. If it still fails, run with verbose cmdstanpy logging:\n"
        "       python3 -m cmdstanpy.install_cmdstan --verbose\n"
        "     and read the actual compiler error near the top of the output --\n"
        "     it is almost always more specific than anything Prophet reports.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    sys.exit(main())