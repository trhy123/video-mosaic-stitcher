import shutil
import subprocess
import sys
from pathlib import Path


root = Path(__file__).resolve().parent.parent
command = [
    sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
    '--windowed', '--onedir', '--name', 'VideoMosaic',
    '--additional-hooks-dir', 'hooks',
    '--exclude-module', 'ssl', '--exclude-module', '_hashlib',
]
if sys.platform == 'darwin':
    command.append('--strip')
subprocess.run([*command, 'video_mosaic.py'], cwd=root, check=True)

bundle = root / 'dist/VideoMosaic'
shutil.copytree(root / 'License', bundle / 'License')
tk_notice = bundle / '_internal/_tk_data/license.terms'
if tk_notice.is_file():
    tk_notice.replace(bundle / 'License/Tk-LICENSE.txt')

if sys.platform == 'linux':
    libraries = [p for p in bundle.rglob('*') if p.is_file() and not p.is_symlink()
                 and (p.name.endswith('.so') or '.so.' in p.name)]
    subprocess.run(['strip', '--strip-debug', bundle / 'VideoMosaic', *libraries], check=True)
