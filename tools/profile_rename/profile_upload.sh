#!/bin/bash
# Unpack a PISCO2 "<date>-<time>_<ID>_PISCO2_png.zip" into the filer profile layout
#   <ID>_PISCO2_<date>-<time>/{..._Images-PNG, ..._Metadata/<name>.{csv,pickle,yaml}, ..._Results}
# Every entry is CRC-checked (zip_salvage.py), the PNGs are compared with the
# frame list of the matching *_png_txt.zip, and the profile is only moved into
# place when nothing is missing. Staging happens next to the target on the filer.
#
#   profile_upload.sh PNG_ZIP [DEST_DIR]   (default DEST_DIR: SO308-PISCO-Profiles on the filer)
set -euo pipefail
ZIP=$1
DEST=${2:-/mnt/filer/SO308/SO308-PISCO-Profiles}
SALVAGE=${SALVAGE:-$HOME/zip_salvage.py}
base=$(basename "$ZIP" _png.zip)           # 20241110-0915_SO308_1-12-1_PISCO2
stamp=${base%%_*}                          # 20241110-0915
idcam=${base#*_}                           # SO308_1-12-1_PISCO2
name=${idcam}_${stamp}                     # SO308_1-12-1_PISCO2_20241110-0915
txtzip=$(dirname "$ZIP")/${base}_png_txt.zip
[ -e "$DEST/$name" ] && { echo "already exists: $DEST/$name"; exit 1; }

stage=$DEST/.uploading_$name
img=$stage/${name}_Images-PNG; meta=$stage/${name}_Metadata
mkdir -p "$img" "$meta" "$stage/${name}_Results"
echo "extracting $ZIP -> $stage"
python3 "$SALVAGE" "$ZIP" --extract "$img" --list "$stage/.list" | tail -3

# metadata out of the image folder, renamed like the other profiles
for f in "$img"/*.pickle "$img"/*.yaml; do mv "$f" "$meta/$name.${f##*.}"; done
mv "$img"/*metadata_settings.csv "$meta/$name.csv"
if ls "$img"/*.BADCRC >/dev/null 2>&1; then echo "CRC errors, not publishing: $stage"; exit 2; fi

# completeness against the txt zip's frame list
missing=$(comm -23 <(unzip -Z1 "$txtzip" | grep '\.txt$' | sed 's|.*/||; s|\.txt$||' | sort) \
                   <(ls "$img" | grep '\.png$' | sed 's/\.png$//' | sort) | wc -l)
echo "PNGs: $(ls "$img" | grep -c '\.png$')   missing vs txt list: $missing"
if [ "$missing" -ne 0 ]; then echo "incomplete, not publishing: $stage"; exit 3; fi
rm "$stage/.list"
mv "$stage" "$DEST/$name"
echo "published $DEST/$name"
