"""Runtime registry for language-specific execution configurations.

This registry maps programming languages to their respective sandbox configurations,
including entrypoints, build steps, and runtime-specific settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Language(StrEnum):
    """Supported programming languages."""
    PYTHON = "python"
    NODEJS = "nodejs"
    CPP = "cpp"


@dataclass
class RuntimeConfig:
    """Configuration for a language runtime."""
    language: Language
    image: str  # Docker image to use
    entrypoint: list[str]  # Base entrypoint for execution
    build_command: list[str] | None = None  # Command to compile/build (if needed)
    build_workdir: str = "/workspace"  # Working directory for build
    file_extension: str = ""  # Expected file extension
    debug_config: dict | None = None  # Debug-specific configuration


# Runtime configurations for each supported language
RUNTIMES = {
    Language.PYTHON: RuntimeConfig(
        language=Language.PYTHON,
        image="concord-sandbox:latest",  # Uses the same image as sandbox.py
        entrypoint=["python", "/workspace/main.py"],
        file_extension=".py",
        debug_config={
            "entrypoint": [
                "python",
                "-m",
                "debugpy",
                "--listen",
                "0.0.0.0:5678",
                "--wait-for-client",
                "/workspace/main.py",
            ],
            "ports": {"5678/tcp": 5678},
        },
    ),
    Language.NODEJS: RuntimeConfig(
        language=Language.NODEJS,
        image="concord-sandbox-nodejs:latest",  # Would need to be built
        entrypoint=["node", "/workspace/main.js"],
        file_extension=".js",
        debug_config={
            "entrypoint": [
                "node",
                "--inspect-brk=0.0.0.0:9229",
                "/workspace/main.js",
            ],
            "ports": {"9229/tcp": 9229},
        },
    ),
    Language.CPP: RuntimeConfig(
        language=Language.CPP,
        image="concord-sandbox-cpp:latest",  # Would need to be built with g++
        entrypoint=["/workspace/main.out"],  # Compiled binary
        build_command=["g++", "-std=c++17", "-O2", "-Wall", "-o", "/workspace/main.out", "/workspace/main.cpp"],
        build_workdir="/workspace",
        file_extension=".cpp",
        debug_config={
            "entrypoint": [
                "gdb",
                "--args",
                "/workspace/main.out",
            ],
            # Note: GDB debugging in containers is more complex and might need additional setup
        },
    ),
}


def get_runtime_config(language: str) -> RuntimeConfig:
    """Get runtime configuration for a language.

    Args:
        language: Language identifier (e.g., "python", "nodejs", "cpp")

    Returns:
        RuntimeConfig for the language

    Raises:
        ValueError: If language is not supported
    """
    try:
        lang_enum = Language(language.lower())
    except ValueError as e:
        supported = [lang.value for lang in Language]
        raise ValueError(
            f"Unsupported language: {language}. "
            f"Supported languages are: {', '.join(supported)}"
        ) from e

    return RUNTIMES[lang_enum]


def get_language_from_file_path(file_path: str) -> str:
    """Detect language from file path extension.

    Args:
        file_path: Path to the file

    Returns:
        Language identifier string

    Raises:
        ValueError: If file extension is not supported
    """
    if not file_path:
        return Language.PYTHON.value  # Default to Python

    # Get file extension
    if "." not in file_path:
        raise ValueError(f"No file extension found in path: {file_path}")

    extension = "." + file_path.split(".")[-1].lower()

    # Map extensions to languages
    extension_map = {
        ".py": Language.PYTHON,
        ".js": Language.NODEJS,
        ".cpp": Language.CPP,
        ".c": Language.CPP,  # Treat C as CPP for simplicity
        ".cc": Language.CPP,
        ".cxx": Language.CPP,
    }

    if extension not in extension_map:
        supported_exts = list(extension_map.keys())
        raise ValueError(
            f"Unsupported file extension: {extension}. "
            f"Supported extensions are: {', '.join(supported_exts)}"
        )

    return extension_map[extension].value


# Files that aren't code (notes, data, README.md) are still editable, just not runnable.
PLAINTEXT = "plaintext"


def language_for_path(file_path: str) -> str:
    """The runtime language for ``file_path``, or ``"plaintext"`` if it can't be run."""
    try:
        return get_language_from_file_path(file_path)
    except ValueError:
        return PLAINTEXT
