# packaging/okt.spec - PyInstaller recipe of the Windows app (folder build, "onedir").
#
# Build (from the repository root, in a venv with setup/requirements_cpu_only.txt + pyinstaller):
#     pyinstaller packaging/okt.spec --noconfirm --clean
# -> dist/OpenKeyTranslate/OpenKeyTranslate.exe + dist/OpenKeyTranslate/_internal/ (code, libraries, frontend).
# The official builds are made by .github/workflows/build-windows.yml from the public source, never on a private PC.
#
# Choices, and why:
#   - a folder, not one file: a one-file exe unpacks ~2 GB to %TEMP% on every start (slow) and is what antivirus
#     heuristics flag most often;
#   - no UPX compression (same reason);
#   - console window: the log is visible and closing the window stops the app (launcher.py);
#   - no models inside: PaddleOCR, the memory model (MiniLM) and the optional LaMa are downloaded on first use into the
#     user's folders, as from the source;
#   - the metadata of every installed package is copied: PaddleX checks its dependencies with importlib.metadata and
#     refuses to start without it; the license files of the packages travel inside the same folders.

import os
import re
import sys
from importlib.metadata import distributions

from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)
# if anything during the build imports the app's code (a hook, a library scanning packages), it must not read or fill
# the builder's own settings.json - the docs-build mode uses defaults and a throwaway folder (core/config.py)
os.environ["OKT_DOCS_BUILD"] = "1"

with open(os.path.join(BACKEND, "core", "config.py"), encoding="utf-8") as f:
    APP_VERSION = re.search(r'^APP_VERSION = "([^"]+)"', f.read(), re.M).group(1)

datas = [
    (os.path.join(ROOT, "frontend"), "frontend"),
    (os.path.join(ROOT, "CHANGELOG.md"), "."),
    (os.path.join(ROOT, "LICENSE"), "."),
]
binaries = []
hiddenimports = []


def _own_modules(pkg: str) -> list[str]:
    """Every module of one of the app's packages, found on disk (importing them here would load the app's config)."""
    out = []
    for dirpath, dirnames, files in os.walk(os.path.join(BACKEND, pkg)):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        dotted = os.path.relpath(dirpath, BACKEND).replace(os.sep, ".")
        out += [dotted if f == "__init__.py" else f"{dotted}.{f[:-3]}" for f in files if f.endswith(".py")]
    return out


# the app's own code: providers, services and utils are partly imported inside functions or by name. routers/ is not
# walked - it also holds old tool scripts; main.py imports every real router by itself.
for pkg in ("core", "models", "services", "utils"):
    hiddenimports += _own_modules(pkg)
hiddenimports += ["scripts.ocr_selftest"]

# libraries that load parts of themselves by name or read their own data files
for pkg in ("paddle", "paddleocr", "paddlex", "uvicorn", "pyphen", "wordfreq", "langcodes", "pyclipper", "shapely",
            "rapidfuzz", "google.genai", "groq", "sentence_transformers", "tokenizers", "safetensors", "ftfy"):
    try:
        d, b, h = collect_all(pkg)
    except Exception as e:                       # a package missing from this venv: say so, the smoke test decides
        print(f"[okt.spec] collect_all({pkg}) skipped: {e}")
        continue
    datas += d
    binaries += b
    hiddenimports += h

# importlib.metadata of every installed distribution (PaddleX dependency checks, version lookups in the app)
for dist in distributions():
    name = dist.metadata["Name"]
    try:
        datas += copy_metadata(name)
    except Exception as e:
        print(f"[okt.spec] metadata of {name} skipped: {e}")

# tools of the developer, never imported by the app (tkinter IS used: the folder pickers of routers/projects + system)
EXCLUDES = ["pdoc", "black", "yapf", "pydoc_markdown", "docspec", "docspec_python", "git_filter_repo",
            "IPython", "pytest", "sacrebleu", "matplotlib", "notebook", "jupyter"]

a = Analysis(
    [os.path.join(BACKEND, "launcher.py")],
    pathex=[BACKEND],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=EXCLUDES,
    noarchive=False,
    # keep the .py sources of these next to the bytecode: they inspect their own code at run time
    module_collection_mode={"paddle": "pyz+py", "paddlex": "pyz+py", "paddleocr": "pyz+py"},
)


def _not_needed(dest: str) -> bool:
    """C/C++ headers and import libraries: only for compiling against a package, never used at run time (~hundreds MB)."""
    d = dest.replace("\\", "/").lower()
    return d.endswith((".h", ".hpp", ".cuh", ".lib", ".a")) or "/include/" in d


a.datas = [x for x in a.datas if not _not_needed(x[0])]
a.binaries = [x for x in a.binaries if not _not_needed(x[0])]

pyz = PYZ(a.pure)


def _version_info():
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)
    nums = tuple((list(map(int, re.findall(r"\d+", APP_VERSION))) + [0, 0, 0, 0])[:4])
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("ProductName", "OpenKeyTranslate"),
            StringStruct("FileDescription", "OpenKeyTranslate - manga / webtoon translation (EN -> PL)"),
            StringStruct("FileVersion", APP_VERSION),
            StringStruct("ProductVersion", APP_VERSION),
            StringStruct("OriginalFilename", "OpenKeyTranslate.exe"),
            StringStruct("LegalCopyright", "Apache License 2.0 - github.com/Weed050/OpenKeyTranslate"),
        ])]), VarFileInfo([VarStruct("Translation", [0x0409, 1200])])],
    )


exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenKeyTranslate",
    icon=os.path.join(ROOT, "frontend", "img", "okt.ico"),
    version=_version_info(),
    console=True,
    upx=False,
    debug=False,
    strip=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="OpenKeyTranslate",
    upx=False,
    strip=False,
)
