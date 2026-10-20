# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules, collect_data_files
from pathlib import Path

block_cipher = None

# Assets and templates to bundle into the standalone executable
datas = [
    ('config', 'config'),
    ('.env.example', '.'),
]
datas += collect_data_files('selenium')

# Required modules and dependencies
hiddenimports = [
    'yaml',
    'dotenv',
    'telegram',
    'telegram.ext',
    'httpx',
    'httpcore',
    'anyio',
    'selenium',
    'selenium.webdriver',
    'selenium.webdriver.chrome',
    'selenium.webdriver.chrome.service',
    'selenium.webdriver.chrome.options',
    'selenium.webdriver.common.by',
    'selenium.webdriver.support.ui',
    'selenium.webdriver.support.expected_conditions',
    'core',
    'core.auth',
    'core.browser',
    'core.config',
    'core.debug',
    'core.selectors',
    'core.session',
    'core.wizard',
    'flows',
    'flows.base',
    'flows.new_booking',
    'flows.reschedule',
    'services',
    'services.approval',
    'services.appuntamento',
    'services.calendar',
    'services.matcher',
    'services.ricette',
    'services.scheduler',
    'services.telegram_bot',
    'state',
    'state.store',
]

hiddenimports += collect_submodules('telegram')
hiddenimports += collect_submodules('selenium')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'numpy',
        'pandas',
        'IPython',
        'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='prenotazioni',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
