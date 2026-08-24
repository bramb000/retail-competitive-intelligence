#!/usr/bin/env bash
# Build .tools/apk/coles/install_ready from stock arm64 splits + patched base.
# Requires arm64 splits under .tools/apk/coles/source/ (see fetch_coles_arm64.py).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$ROOT/.tools"
SRC="$TOOLS/apk/coles/source"
OUT="$TOOLS/apk/coles/install_ready"
KS="$TOOLS/debug.keystore"
APKSIGNER=$(ls ~/Library/Android/sdk/build-tools/*/apksigner 2>/dev/null | tail -1)
ZIPALIGN=$(ls ~/Library/Android/sdk/build-tools/*/zipalign 2>/dev/null | tail -1)

if [[ ! -f "$KS" ]]; then
  keytool -genkey -v -keystore "$KS" -storepass android -alias androiddebugkey \
    -keypass android -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=Android Debug,O=Android,C=US"
fi

pick() {
  local pattern="$1"
  find "$SRC" -maxdepth 1 -name "$pattern" ! -name "*.idsig" | sort | tail -1
}

BASE_PATCHED=$(pick "base-patched-signed*.apk")
[[ -z "$BASE_PATCHED" ]] && BASE_PATCHED=$(pick "base-patched*.apk")
ARM64=$(pick "split_config.arm64*.apk")
HDPI=$(pick "split_config.hdpi*.apk")
XHDPI=$(pick "split_config.xhdpi*.apk")
XXHDPI=$(pick "split_config.xxhdpi*.apk")

if [[ -z "$BASE_PATCHED" || -z "$ARM64" ]]; then
  echo "Missing patched base or arm64 split in $SRC" >&2
  echo "Run: .venv/bin/python scripts/fetch_coles_arm64.py" >&2
  exit 1
fi

DPI="$XXHDPI"; [[ -z "$DPI" && -n "$XHDPI" ]] && DPI="$XHDPI"; [[ -z "$DPI" && -n "$HDPI" ]] && DPI="$HDPI"
rm -rf "$OUT"
mkdir -p "$OUT"

sign_split() {
  local src="$1" dst="$2"
  local tmp="$OUT/.$dst.aligned"
  cp "$src" "$OUT/$dst"
  "$ZIPALIGN" -f -p 4 "$OUT/$dst" "$tmp"
  mv "$tmp" "$OUT/$dst"
  "$APKSIGNER" sign --ks "$KS" --ks-pass pass:android --key-pass pass:android \
    --out "$OUT/$dst" "$OUT/$dst"
}

cp "$BASE_PATCHED" "$OUT/base.apk"
"$ZIPALIGN" -f -p 4 "$OUT/base.apk" "$OUT/.base.aligned"
mv "$OUT/.base.aligned" "$OUT/base.apk"
"$APKSIGNER" sign --ks "$KS" --ks-pass pass:android --key-pass pass:android \
  --out "$OUT/base.apk" "$OUT/base.apk"
sign_split "$ARM64" "split_config.arm64_v8a.apk"
if [[ -n "$DPI" ]]; then
  out_name="$(basename "$DPI")"
  sign_split "$DPI" "$out_name"
fi

echo "install_ready:"
ls -la "$OUT"
