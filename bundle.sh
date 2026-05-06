#!/bin/bash
set -e

APP_NAME="steelg8"
APP_DIR=".build/steelg8.app"
APP_DIR_ABS="$(cd "$(dirname "${APP_DIR}")" && pwd)/$(basename "${APP_DIR}")"
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

# 运行中的 LSUIElement app 不会因为 open 自动换成新二进制；先停旧实例，
# 否则容易出现“打包成功但实际还是旧 app / 旧 Python 内核”的错觉。
if pgrep -x "${APP_NAME}" >/dev/null 2>&1; then
    echo "🛑 关闭正在运行的 ${APP_NAME}..."
    pkill -x "${APP_NAME}" || true
    for _ in {1..30}; do
        if ! pgrep -x "${APP_NAME}" >/dev/null 2>&1; then
            break
        fi
        sleep 0.2
    done
fi

KERNEL_PATTERN="${APP_DIR_ABS}/Contents/Resources/Python/server.py"
if pgrep -f "${KERNEL_PATTERN}" >/dev/null 2>&1; then
    echo "🛑 清理旧 Python 内核..."
    pkill -f "${KERNEL_PATTERN}" || true
    for _ in {1..30}; do
        if ! pgrep -f "${KERNEL_PATTERN}" >/dev/null 2>&1; then
            break
        fi
        sleep 0.2
    done
fi

# Ensure .app skeleton + always rewrite Info.plist (idempotent)
mkdir -p "${MACOS}" "${RESOURCES}"

# 每次都重写 Info.plist，避免老 app 没 NSAppleEventsUsageDescription
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
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSAppleEventsUsageDescription</key>
    <string>steelg8 需要调用 Apple 备忘录来把捕获台内容存到你指定的文件夹里，iCloud 自动同步到手机。</string>
    <key>CFBundleIconName</key>
    <string>AppIcon</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
</dict>
</plist>
PLIST

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

# App 图标
if [ -f "assets/AppIcon.icns" ]; then
    cp assets/AppIcon.icns "${RESOURCES}/AppIcon.icns"
fi

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
