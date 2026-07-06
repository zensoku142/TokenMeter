# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # pyqtgraph 的通用 hook 会收集绘图、3D、QML、测试等可选后端；本项目只使用
    # QWidget/PlotWidget，排除这些模块可避免捆入 QtQuick、QtPdf 和 QtTest。
    excludes=[
        'tkinter', 'pystray', 'PIL',
        'matplotlib', 'scipy', 'OpenGL', 'cupy', 'colorcet',
        'pyqtgraph.console', 'pyqtgraph.examples', 'pyqtgraph.flowchart',
        'pyqtgraph.imageview', 'pyqtgraph.jupyter', 'pyqtgraph.multiprocess',
        'pyqtgraph.opengl', 'pyqtgraph.parametertree',
        'PySide6.QtNetwork', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtPdf',
        'PySide6.QtPdfWidgets', 'PySide6.QtQml', 'PySide6.QtQuick',
        'PySide6.QtQuickControls2', 'PySide6.QtQuickWidgets', 'PySide6.QtTest',
        'PySide6.QtVirtualKeyboard',
    ],
    noarchive=False,
    optimize=0,
)

# PySide6 的 hook 会把可选 Qt DLL/插件作为二进制再次加入；模块排除不会自动
# 移除它们。以下组件只服务于 QML/Quick/PDF/OpenGL/虚拟键盘或非 Windows
# 测试平台，当前 QWidget + CPU 绘图路径不会加载。
unused_qt_prefixes = (
    'PySide6\\Qt6Network.dll',
    'PySide6\\Qt6OpenGL.dll',
    'PySide6\\Qt6Pdf.dll',
    'PySide6\\Qt6Qml',
    'PySide6\\Qt6Quick.dll',
    'PySide6\\Qt6VirtualKeyboard.dll',
    'PySide6\\opengl32sw.dll',
    'PySide6\\plugins\\generic\\',
    'PySide6\\plugins\\platforminputcontexts\\',
    'PySide6\\plugins\\platforms\\qdirect2d.dll',
    'PySide6\\plugins\\platforms\\qminimal.dll',
    'PySide6\\plugins\\platforms\\qoffscreen.dll',
)
a.binaries = [
    item for item in a.binaries
    if not item[0].replace('/', '\\').startswith(unused_qt_prefixes)
]
a.datas = [
    item for item in a.datas
    if not item[0].replace('/', '\\').startswith(unused_qt_prefixes)
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TokenSpider-v1.1.0-windows-x64',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    version='version_info.txt',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
