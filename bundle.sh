#!/bin/bash
set -e

APP_NAME="steelg8"
APP_DIR=".build/steelg8.app"
CONTENTS="${APP_DIR}/Contents"
MACOS="${CONTENTS}/MacOS"
RESOURCES="${CONTENTS}/Resources"

VENV_DIR=".venv"
REQ_FILE="Python/requirements.txt"
VENV_STAMP="${VENV_DIR}/.installed-stamp"

# 1. 准备 Python venv（Phase 2 起引入 pip 依赖：python-docx 等）
if [ ! -d "${VENV_DIR}" ]; then
    echo "🐍 创建 venv..."
    python3 -m venv "${VENV_DIR}"
fi
# 只有 requirements.txt 比 stamp 新才重新安装，省事
if [ "${REQ_FILE}" -nt "${VENV_STAMP}" ]; then
    echo "🐍 安装/更新 Python 依赖..."
    "${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
    "${VENV_DIR}/bin/pip" install -r "${REQ_FILE}"
    touch "${VENV_STAMP}"
fi

# 2. Swift Build
echo "🔨 编译中..."
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  /Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/swift build 2>&1

# Create .app bundle (only if missing)
if [ ! -d "${APP_DIR}" ]; then
    echo "📦 创建 App Bundle..."
    mkdir -p "${MACOS}" "${RESOURCES}"

    cat > "${CONTENTS}/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>com.local.steelg8</string>
    <key>CFBundleName</key>
    <string>steelg8</string>
    <key>CFBundleDisplayName</key>
    <string>steelg8</string>
    <key>CFBundleExecutable</key>
    <string>steelg8</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST
fi

# Update executable
echo "📦 更新可执行文件..."
cp ".build/debug/${APP_NAME}" "${MACOS}/${APP_NAME}"

# Copy Python kernel + prompts + config + Web assets so .app is self-contained
echo "📦 复制 Python / Web 资源..."
rm -rf "${RESOURCES}/Python" "${RESOURCES}/Web" "${RESOURCES}/prompts" "${RESOURCES}/config"
mkdir -p "${RESOURCES}/Python" "${RESOURCES}/Web/chat" "${RESOURCES}/prompts" "${RESOURCES}/config"
cp -R Python/.       "${RESOURCES}/Python/"
cp -R Web/chat/.     "${RESOURCES}/Web/chat/" 2>/dev/null || true
cp prompts/*         "${RESOURCES}/prompts/"
cp config/*          "${RESOURCES}/config/"

# venv 带进 .app 里，保证 app 启动时能找到依赖
rm -rf "${RESOURCES}/.venv"
cp -R "${VENV_DIR}" "${RESOURCES}/.venv"

# Sign
echo "🔏 签名..."
codesign --force --sign - "${APP_DIR}"

# Ensure symlink in /Applications
if [ ! -L "/Applications/steelg8.app" ]; then
    rm -rf /Applications/steelg8.app
    ln -s "$(cd "$(dirname "${APP_DIR}")" && pwd)/steelg8.app" /Applications/steelg8.app
fi

echo "✅ 构建完成"
echo "🚀 启动应用..."
open /Applications/steelg8.app
