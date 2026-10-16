#!/usr/bin/env python3
"""
PuffPAI Setup Script
"""

from setuptools import setup, find_packages
import os

# Read README for long description
with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

# Read requirements
with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="puffpai",
    version="0.1.0-alpha",
    author="Cypher Sisters",
    # No packaging contact address on purpose: SECURITY.md routes reports
    # through the GitHub org rather than an inbox, and an address baked into a
    # package is one more thing that has to stay true. The absence is enforced
    # tree-wide by tests/test_repo_hygiene_docs.py.
    description="Privacy and User-Focused Fortified Personal AI - local-first personal AI with smart home integration",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/CypherSisters/PuffPAIBuildBot",
    project_urls={
        "Bug Reports": "https://github.com/CypherSisters/PuffPAIBuildBot/issues",
        "Source": "https://github.com/CypherSisters/PuffPAIBuildBot",
        "Documentation": "https://github.com/CypherSisters/PuffPAIBuildBot/tree/main/docs",
    },
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: End Users/Desktop",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Home Automation",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Operating System :: POSIX :: Linux",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: MacOS",
    ],
    python_requires=">=3.11",
    install_requires=requirements,
    extras_require={
        # Offline voice pipeline (mic -> wake word -> Vosk -> LLM -> Piper).
        # All are optional at runtime: without them the voice stack falls back
        # to stub engines and `puffpai voice test` reports what is missing.
        "voice": [
            "sounddevice>=0.4.6,<1.0",
            "vosk>=0.3.44",
            "piper-tts>=1.2.0",
            "numpy>=1.24",
        ],
        "dev": [
            "pytest>=7.4.0",
            "black>=23.0.0",
            "flake8>=6.1.0",
            "mypy>=1.5.0",
        ],
        "desktop": [
            "pyinstaller>=6.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "puffpai=puffpai.cli:main",
        ],
    },
    include_package_data=True,
    package_data={
        "puffpai": [
            "config/*.json.template",
            "config/*.txt",
            "data/workout_library.json",
            # The built-in wizard packages. They are laid out exactly like a
            # published package (a folder with a manifest and its code, no
            # __init__.py), so find_packages does not see them and they have to
            # be named here. Where they are installed is what makes them
            # built-in -- see puffpai/wizard/loader.py.
            "wizard/packages/*/manifest.json",
            "wizard/packages/*/skill.py",
            "wizard/packages/*/card.json",
            # The locked box's files, streamed into the box when it is
            # set up, and the browser-app runner.
            "box/assets/*",
            "webapps/runner/*.mjs",
            "webapps/runner/shim.js",
        ],
    },
)
