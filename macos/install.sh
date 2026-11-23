#!/bin/bash
# Runs from the bundled native installer; requires no Python or developer tools.
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
resources="$1"
test_root="${2:-}"
version="__MORI_VERSION__"
stage=""
mount=""
backup=""
mutating=0
installed_names=()
old_names=()

progress() { printf 'MORI_PROGRESS|%s|%s\n' "$1" "$2"; }
fail() { printf 'MORI_ERROR|%s\n' "$1" >&2; exit 1; }
cleanup() {
    status=$?
    if [[ "$status" != 0 && "$mutating" == 1 ]]; then
        for name in "${installed_names[@]}"; do
            if [[ -e "$plugin_root/$name" && ! -L "$plugin_root/$name" ]]; then
                mv "$plugin_root/$name" "$stage/failed-$name" || true
            fi
        done
        for name in "${old_names[@]}"; do
            if [[ -e "$backup/$name" && ! -e "$plugin_root/$name" ]]; then
                ditto "$backup/$name" "$plugin_root/$name" || true
            fi
        done
        if [[ -f "$backup/kritarc" ]]; then cp -p "$backup/kritarc" "$config" || true; fi
    fi
    if [[ -n "$mount" ]]; then hdiutil detach -quiet "$mount" || true; fi
    case "$stage" in "${TMPDIR:-/tmp/}"mori-mac-install.*) rm -rf "$stage" ;; esac
    exit "$status"
}
trap cleanup EXIT
[[ "$(uname -s)" == Darwin ]] || fail '请在 Mac 上运行此安装包。'
if [[ -z "$test_root" ]]; then
    pgrep -x krita >/dev/null && fail '请先保存作品并完全退出 Krita，再点击安装。'
    profile="$HOME"
else
    [[ "$test_root" = /* && "$test_root" != / && ! -L "$test_root" ]] || fail '测试路径无效。'
    profile="$test_root/profile"
fi
config="$profile/Library/Preferences/kritarc"
resource_root="$profile/Library/Application Support/krita"
if [[ -z "$test_root" && -f "$config" ]]; then
    custom=$(awk -F= '/^ResourceDirectory=/{sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$config")
    if [[ -n "$custom" ]]; then
        [[ "$custom" = /* && "$custom" != / ]] || fail 'Krita 资源目录无效，请先在 Krita 中确认资源位置。'
        resource_root="$custom"
    fi
fi
plugin_root="$resource_root/pykrita"
mkdir -p "$plugin_root" "$(dirname "$config")" "$profile/Applications/MoriKrita"
for name in mori_krita mori_krita.desktop ai_diffusion ai_diffusion.desktop; do
    [[ ! -L "$plugin_root/$name" ]] || fail '检测到插件链接，请使用普通 Krita 资源目录后重试。'
done
stage=$(mktemp -d "${TMPDIR:-/tmp/}mori-mac-install.XXXXXXXX")
progress 5 '正在校验安装包'
actual=$(shasum -a 256 "$resources/plugins.zip" | awk '{print $1}')
[[ "$actual" == __PLUGINS_SHA256__ ]] || fail '插件安装包校验失败，请重新下载。'
ditto -x -k "$resources/plugins.zip" "$stage/payload"
while IFS=$'\t' read -r digest relative; do
    [[ -n "$digest" && "$relative" != /* && "$relative" != *..* ]] || fail '安装清单路径无效。'
    actual=$(shasum -a 256 "$stage/payload/$relative" | awk '{print $1}')
    [[ "$actual" == "$digest" ]] || fail '插件文件校验失败，请重新下载。'
done < "$stage/payload/files.sha256"
progress 20 '正在准备 Krita'
actual=$(shasum -a 256 "$resources/krita.dmg" | awk '{print $1}')
[[ "$actual" == 531e261da1fd8d5c468cfcd4eebd16e9edf6ab320e89406d2875f2e43c1148de ]] || fail 'Krita 安装文件校验失败。'
mount="$stage/krita-volume"
hdiutil attach -quiet -readonly -nobrowse -mountpoint "$mount" "$resources/krita.dmg"
codesign --verify --deep --strict "$mount/krita.app" || fail 'Krita 官方签名校验失败。'
app="$profile/Applications/MoriKrita/Krita-5.3.4.1-Mori-$version.app"
[[ ! -L "$app" ]] || fail '安装位置是链接，无法继续安装。'
if [[ -e "$app" ]]; then
    [[ -f "$profile/Applications/MoriKrita/.mori-$version" ]] || fail '目标位置已有其他程序，请先选择其他位置。'
    codesign --verify --deep --strict "$app" || fail '现有 Krita 文件校验失败，请联系维护者。'
else
    ditto "$mount/krita.app" "$stage/krita.app"
    mv "$stage/krita.app" "$app"
fi
hdiutil detach -quiet "$mount"
mount=""
progress 55 '正在备份现有插件'
stamp="$(date +%Y%m%d-%H%M%S)-$$"
backup="$resource_root/bundle-backups/Mori-$version-$stamp"
mkdir -p "$backup"
if [[ -f "$config" ]]; then cp -p "$config" "$backup/kritarc"; fi
for name in mori_krita mori_krita.desktop ai_diffusion ai_diffusion.desktop; do
    if [[ -e "$plugin_root/$name" ]]; then
        ditto "$plugin_root/$name" "$backup/$name"
        old_names+=("$name")
    fi
done
mutating=1
progress 65 '正在安装 Mori 与 AI Diffusion'
for name in mori_krita mori_krita.desktop ai_diffusion ai_diffusion.desktop; do
    if [[ -e "$plugin_root/$name" ]]; then mv "$plugin_root/$name" "$stage/old-$name"; fi
    installed_names+=("$name")
    ditto "$stage/payload/plugins/$name" "$plugin_root/$name"
done
mkdir -p "$resource_root/actions" "$resource_root/windowlayouts"
for action in "$stage/payload/actions/"*; do [[ ! -f "$action" ]] || cp -p "$action" "$resource_root/actions/"; done
# Enable only the supplied plugins, preserving all other preferences and sections.
touch "$config"
awk '
BEGIN { inpython=0; seen=0 }
/^\[python\]\r?$/ { print; print "enable_mori_krita=true"; print "enable_ai_diffusion=true"; inpython=1; seen=1; next }
/^\[/ { inpython=0 }
inpython && /^enable_(plugin_)?(mori_krita|ai_diffusion)=/ { next }
{ print }
END { if (!seen) print "\n[python]\nenable_mori_krita=true\nenable_ai_diffusion=true" }
' "$config" > "$stage/kritarc"
cp "$stage/kritarc" "$config"
printf '%s\n' "$app" > "$profile/Applications/MoriKrita/.mori-$version"
printf '%s\n' "$app" > "$profile/Applications/MoriKrita/current-app.txt"
mutating=0
progress 100 '安装完成，可打开 Krita 开始创作'
printf 'MORI_RESULT|%s\n' "$app"
printf 'MORI_BACKUP|%s\n' "$backup"
