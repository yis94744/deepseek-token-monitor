# -*- mode: python ; coding: utf-8 -*-

# 运行时解压目录：保持 PyInstaller 默认（%TEMP%\_MEI<pid>）。
# 不要设置自定义 runtime_tmpdir：其路径在构建时被写死（含构建机用户名），
# 且引导程序只创建叶子目录——在全新电脑上父目录不存在会直接报
# "LOADER: failed to create runtime-tmpdir path ... CreateDirectory"。
# %TEMP% 对任何登录用户必然存在，是 PyInstaller 的默认且最稳妥的选择。

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
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
