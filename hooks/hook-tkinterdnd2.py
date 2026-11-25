"""Bundle the native TkDND files for the Python interpreter used to build."""
import os
import platform
import tkinter
from pathlib import Path

from PyInstaller.utils.hooks import get_package_paths

system = platform.system()
machine = os.environ.get('PROCESSOR_ARCHITECTURE', platform.machine()) if system == 'Windows' else platform.machine()
platform_names = {
    ('Windows', 'x86'): 'win-x86', ('Windows', 'AMD64'): 'win-x64', ('Windows', 'ARM64'): 'win-arm64',
    ('Linux', 'x86_64'): 'linux-x64', ('Linux', 'aarch64'): 'linux-arm64', ('Darwin', 'arm64'): 'osx-arm64',
    ('Darwin', 'x86_64'): 'osx-x64',
}
name = platform_names[(system, machine)]
_, package_path = get_package_paths('tkinterdnd2')
tkdnd = Path(package_path) / 'tkdnd'
if int(str(tkinter.TclVersion).split('.')[0]) >= 9 and (tkdnd / (name + '-tcl9')).is_dir():
    name += '-tcl9'
source = tkdnd / name
if not source.is_dir():
    raise RuntimeError(f'Missing TkDND files: {source}')
destination = f'tkinterdnd2/tkdnd/{name}'
datas = [(str(p), destination) for p in source.glob('*.tcl')]
binaries = [(str(p), destination) for ext in ('*.dll', '*.so', '*.dylib') for p in source.glob(ext)]
if not binaries:
    raise RuntimeError(f'Missing TkDND native library: {source}')
