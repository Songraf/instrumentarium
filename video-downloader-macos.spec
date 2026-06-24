# Video Downloader — PyInstaller spec for macOS
# Build: pyinstaller video-downloader-macos.spec --clean
# Produces: dist/Instrumentarium.app (proper macOS .app bundle)

block_cipher = None

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
        ('assets/Info.plist', 'Contents/Resources'),
        ('assets/entitlements.plist', 'Contents/Resources'),
        ('assets/icon.icns', 'Contents/Resources'),
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
    upx=False,        # upx can break macOS codesign
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,    # macOS: handle Apple Events (e.g. file open)
    target_arch='universal2',  # Intel + Apple Silicon
    codesign_identity='',
    entitlements_file='assets/entitlements.plist',
    icon='assets/icon.icns',
)

# ── .app Bundle ──────────────────────────────────────────────
# BUNDLE creates: Instrumentarium.app/Contents/MacOS/Instrumentarium (exe)
#                Instrumentarium.app/Contents/Resources/icon.icns
#                Instrumentarium.app/Contents/Info.plist
app = BUNDLE(
    exe,
    name='Instrumentarium.app',
    icon='assets/icon.icns',
    bundle_identifier='com.instrumentarium.app',
    info_plist={
        'CFBundleName': 'Instrumentarium',
        'CFBundleDisplayName': 'Instrumentarium',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'LSMinimumSystemVersion': '10.14',
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,
    },
)
