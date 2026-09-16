"""Safe credential materialization for hosted collectors."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional


@contextmanager
def materialize_google_credentials(
    *,
    gsc_json: Optional[str] = None,
    ga4_json: Optional[str] = None,
    gsc_env_var: str = "GSC_CREDENTIALS_JSON",
    ga4_env_var: str = "GA4_CREDENTIALS_JSON",
) -> Iterator[Dict[str, str]]:
    """Write credential JSON from env/secrets to temp files with mode 0600.

    Yields a mapping of env var overrides for GSC_CREDENTIALS_PATH / GA4_CREDENTIALS_PATH.
    Temp files are removed on exit.
    """
    created: List[Path] = []
    overrides: Dict[str, str] = {}
    try:
        gsc_payload = gsc_json if gsc_json is not None else os.getenv(gsc_env_var, "")
        ga4_payload = ga4_json if ga4_json is not None else os.getenv(ga4_env_var, "")
        if gsc_payload.strip():
            path = _write_secret_file(gsc_payload, prefix="gsc_creds_")
            created.append(path)
            overrides["GSC_CREDENTIALS_PATH"] = str(path)
        if ga4_payload.strip():
            path = _write_secret_file(ga4_payload, prefix="ga4_creds_")
            created.append(path)
            overrides["GA4_CREDENTIALS_PATH"] = str(path)
        yield overrides
    finally:
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _write_secret_file(payload: str, *, prefix: str) -> Path:
    # Validate JSON before writing so we never leave partial garbage secrets.
    json.loads(payload)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=prefix,
        suffix=".json",
        delete=False,
    )
    path = Path(handle.name)
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.chmod(path, 0o600)
    return path
