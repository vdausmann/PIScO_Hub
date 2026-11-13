#!/bin/bash
# Upload a PISCO2 profile whose PNG zip is truncated: the PNGs that survive go to
# <name>_Images-PNG-PARTIAL, the frames missing as PNG are filled from the intact
# JPG zip into <name>_Images-JPG-FILL, and README_INCOMPLETE_PNG.txt explains it.
# The folder names differ from the normal _Images-PNG on purpose, so a pipeline
# expecting a complete PNG profile notices.
#
#   profile_upload_partial.sh PNG_ZIP [DEST_DIR]
#   (expects <base>_jpg.zip and <base>_png_txt.zip next to PNG_ZIP)
set -euo pipefail
ZIP=$1
DEST=${2:-/mnt/filer/SO308/SO308-PISCO-Profiles}
SALVAGE=${SALVAGE:-$HOME/zip_salvage.py}
src=$(dirname "$ZIP")
base=$(basename "$ZIP" _png.zip)           # 20241110-0915_SO308_1-12-1_PISCO2
stamp=${base%%_*}
idcam=${base#*_}
name=${idcam}_${stamp}                     # SO308_1-12-1_PISCO2_20241110-0915
JZIP=$src/${base}_jpg.zip
TXTZIP=$src/${base}_png_txt.zip
[ -e "$DEST/$name" ] && { echo "already exists: $DEST/$name"; exit 1; }

stage=$DEST/.uploading_$name
P=$stage/${name}_Images-PNG-PARTIAL
J=$stage/${name}_Images-JPG-FILL
M=$stage/${name}_Metadata
W=$stage/.work
mkdir -p "$P" "$J" "$M" "$stage/${name}_Results" "$W"

echo "[$(date +%T)] PNGs from $ZIP"
python3 "$SALVAGE" "$ZIP" --extract "$P" --list "$W/png.list" | tail -3
for f in "$P"/*.BADCRC; do [ -e "$f" ] && { echo "CRC error: $f"; exit 2; }; done
mv "$P"/*_png.pickle "$M/$name.pickle"
mv "$P"/*_png.yaml   "$M/$name.yaml"
rm -f "$P"/*.csv                            # truncated zips lose it; taken from the jpg zip

unzip -Z1 "$TXTZIP" | grep '\.txt$' | sed 's|.*/||; s|\.txt$||' | sort > "$W/all"
ls "$P" | grep '\.png$' | sed 's/\.png$//' | sort > "$W/png"
comm -23 "$W/all" "$W/png" > "$W/missing"
echo "frames $(wc -l < "$W/all"), PNG $(wc -l < "$W/png"), to fill from JPG $(wc -l < "$W/missing")"
[ "$(comm -13 "$W/all" "$W/png" | wc -l)" -eq 0 ] || { echo "PNGs not in the frame list"; exit 3; }

echo "[$(date +%T)] JPG fill from $JZIP"
python3 "$SALVAGE" "$JZIP" --extract "$J" --only "$W/missing" | tail -3
for f in "$J"/*.BADCRC; do [ -e "$f" ] && { echo "CRC error: $f"; exit 2; }; done
mv "$J"/*metadata_settings.csv "$M/$name.csv"
mv "$J"/*_jpg.pickle "$M/${name}_jpg.pickle"
mv "$J"/*_jpg.yaml   "$M/${name}_jpg.yaml"
ls "$J" | grep '\.jpg$' | sed 's/\.jpg$//' | sort > "$W/jpg"
cmp -s "$W/jpg" "$W/missing" || { echo "JPG fill does not match the missing frames"; exit 4; }

sed 's/$/.png/' "$W/png" > "$M/frames_png.txt"
sed 's/$/.jpg/' "$W/jpg" > "$M/frames_jpg_fill.txt"
cat > "$stage/README_INCOMPLETE_PNG.txt" <<EOF
$name: PNG SET INCOMPLETE, gaps filled with JPG

The only PNG archive of this profile (${base}_png.zip, on the SO308-2
USB SSD and its copy in T710_data/PISCO_SO308_TemporarySave) is truncated:
it ends mid-image, without its central directory (probably cut off while
being written: the SSD is full).

  frames recorded (from ${base}_png_txt.zip):  $(wc -l < "$W/all")
  recovered as PNG, CRC-checked:  $(wc -l < "$W/png")  -> ${name}_Images-PNG-PARTIAL/
  missing as PNG, filled as JPG:  $(wc -l < "$W/jpg")  -> ${name}_Images-JPG-FILL/
                                  (from the intact ${base}_jpg.zip)

The missing frames are spread evenly over the whole depth range (zip order is
not depth order). PNG + JPG-FILL together give every frame exactly once.
JPG frames are lossy-compressed: segmentation/measurements on them may differ
slightly from PNG frames. Lists: ${name}_Metadata/frames_png.txt, frames_jpg_fill.txt.

Metadata: $name.yaml/.pickle are the PNG ones (they list ALL frames as .png,
including the ones only present as JPG); ${name}_jpg.yaml/.pickle are the JPG
ones; $name.csv comes from the JPG zip (the PNG zip lost it).
Segmentation results computed at sea from the full PNG set:
SO308-Results/${stamp%%-*}/${base}_png_Segmentation_results.zip

Assembled $(date +%F) from the truncated PNG zip with zip_salvage.py.
EOF
rm -r "$W"
mv "$stage" "$DEST/$name"
echo "[$(date +%T)] published $DEST/$name"
