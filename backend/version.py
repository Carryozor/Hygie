"""Version Hygie — read from HYGIE_VERSION env var (set via Docker --build-arg VERSION=X.Y.Z)."""
import os

VERSION = os.environ.get("HYGIE_VERSION", "2.8.0")
