# Video Downloader — PyInstaller spec for Windows
# Build on Windows: pyinstaller video-downloader-win.spec

block_cipher = None

# Collect all pywebview submodules (needed for edgechromium on Windows)
try:
    from PyInstaller.utils.hooks import collect_all
    webview_datas, webview_binaries, webview_hiddenimports = collect_all('webview')
except Exception:
    webview_datas, webview_binaries, webview_hiddenimports = [], [], ['webview']

a = Analysis(
    ['app.py', 'server_main.py'],
    pathex=[],
    binaries=webview_binaries,
    datas=webview_datas + [
        ('download.html', '.'),
        ('server/', 'server'),
    ],
    hiddenimports=webview_hiddenimports + [
        'bottle', 'proxy_tools',
        'server', 'server.state', 'server.utils', 'server.errors', 'server.setup', 'server.download',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='Instrumentarium',
    version='1.2.0',
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
    icon='assets/icon.ico',
)