# -*- mode: python ; coding: utf-8 -*-

import os

# 单文件模式运行时解压目录：固定到应用自己的目录，避免 %TEMP% 被
# 安全软件/受限环境拦截导致 "Failed to extract VCRUNTIME140.dll"。
_runtime_tmp = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "DeepSeekTokenMonitor", "_runtime")

a = Analysis(
    ['token_monitor.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DeepSeekTokenMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=_runtime_tmp,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
