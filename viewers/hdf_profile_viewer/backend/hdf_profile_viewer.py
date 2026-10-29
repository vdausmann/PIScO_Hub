import h5py
import os
import threading
from collections import OrderedDict
import numpy as np
import io
import base64
import re
import numpy as np
from flask import jsonify
from PIL import Image
from pathlib import Path
import cv2


# Frames carry no depth attribute; it is in the name the camera wrote, e.g.
# "M202_028-02_PISCO2_0009.94dbar-38.58N-027.32W-22.18C_...".
_DEPTH_IN_NAME = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*dbar", re.IGNORECASE)


# A profile only changes while its segmenter is still writing it, and a
# PISCO3 cast has tens of thousands of frames to count, so the counts are
# kept per file and recomputed only when the file's size or mtime moves.
_PROFILE_CACHE_SIZE = 16
_profile_cache = OrderedDict()
_profile_lock = threading.Lock()


def profile_counts(file_path: str):
    """Object count per frame, with the depth each frame was taken at.

    This is the profile as a whole rather than one frame of it: how many
    particles were found at each depth, which is what the plot beside the
    preview draws.
    """
    st = os.stat(file_path)
    stamp = (st.st_mtime_ns, st.st_size)
    with _profile_lock:
        cached = _profile_cache.get(file_path)
        if cached and cached[0] == stamp:
            _profile_cache.move_to_end(file_path)
            return cached[1]

    result = _count_profile(file_path)

    with _profile_lock:
        _profile_cache[file_path] = (stamp, result)
        _profile_cache.move_to_end(file_path)
        while len(_profile_cache) > _PROFILE_CACHE_SIZE:
            _profile_cache.popitem(last=False)
    return result


def _count_profile(file_path: str):
    points = []
    with h5py.File(file_path, "r") as f:
        for name in f.keys():
            group = f[name]
            if not isinstance(group, h5py.Group):
                continue
            # One lookup instead of a membership test and then a second open.
            widths = group.get("boundingBoxW")
            if not isinstance(widths, h5py.Dataset):
                continue

            match = _DEPTH_IN_NAME.search(name)
            points.append({
                "name": name,
                "depth": float(match.group(1)) if match else None,
                "count": int(widths.shape[0]),
            })

    # Deepest last. Frames whose name carries no depth keep file order at the
    # end rather than being dropped, so nothing silently disappears.
    with_depth = sorted((p for p in points if p["depth"] is not None),
                        key=lambda p: p["depth"])
    without = [p for p in points if p["depth"] is None]

    return {
        "points": with_depth + without,
        "with_depth": len(with_depth),
        "without_depth": len(without),
    }


# How many children one request lists. A PISCO3 cast has tens of thousands of
# frame groups; walking all of them, with their datasets, took longer than the
# server allows a request and gave the browser half a million nodes to build.
CHILDREN_PAGE = 200


def list_children(file_path: str, group_path: str = "/", offset: int = 0,
                  limit: int = CHILDREN_PAGE, query: str = None):
    """One page of a group's direct children, optionally filtered by name.

    Only the names are read for the whole group, which is cheap; what each
    child is (group or dataset) is looked up for the page that is sent.
    Raises KeyError when the group does not exist.
    """
    with h5py.File(file_path, "r") as f:
        group = f[group_path]
        if not isinstance(group, h5py.Group):
            raise KeyError(f"{group_path} is not a group")

        names = list(group.keys())
        if query:
            needle = query.lower()
            names = [n for n in names if needle in n.lower()]

        offset = max(0, offset)
        page = names[offset:offset + max(1, limit)]
        prefix = group.name.rstrip("/")
        children = []
        for n in page:
            kind = group.get(n, getclass=True)
            children.append({
                "name": f"{prefix}/{n}",
                "type": "group" if kind is h5py.Group else "dataset",
            })

        return {
            "group": group.name,
            "total": len(names),
            "offset": offset,
            "children": children,
        }


def get_attributes(file_path: str, path: str):

    with h5py.File(file_path, "r") as f:

        group = f[path]

        if not isinstance(group, h5py.Group) and not isinstance(group,  h5py.Dataset):
            return None

        attributes = dict(group.attrs.items())

        return attributes


def get_dataset_info(file_path: str, dataset_path: str):
    with h5py.File(file_path, "r") as f:

        ds = f[dataset_path]

        if not isinstance(ds, h5py.Dataset):
            return None

        group_path = "/".join(ds.name.split("/")[:-1])
        group_attrs = get_attributes(file_path, group_path)

        group_attrs_str = ""
        if not group_attrs is None:
            for key, val in group_attrs.items():
                group_attrs_str += f"{key}: {val}\n"

        ds_attrs = get_attributes(file_path, dataset_path)

        ds_attrs_str = ""
        if not ds_attrs is None:
            for key, val in ds_attrs.items():
                ds_attrs_str += f"{key}: {val}\n"


        info = {
            "group_attrs": group_attrs_str,
            "dataset_attrs": ds_attrs_str,
            "name": ds.name,
            "shape": list(ds.shape),
            "dtype": str(ds.dtype),
            "size": ds.size,
        }

        return info


def get_dataset_data(file_path: str, dataset_path: str):
    with h5py.File(file_path, "r") as f:

        ds = f[dataset_path]

        if not isinstance(ds, h5py.Dataset):
            return None

        info = {
            "shape": list(ds.shape),
            "data": ds[:].tolist()
        }

        return info


def generate_crop(file_path: str, element_index: int, dataset_path: str,
                  isolate: bool = False):
    group_path = "/".join(dataset_path.split("/")[:-1])

    offsets = get_dataset_data(file_path, group_path + "/offsets")
    heights = get_dataset_data(file_path, group_path + "/boundingBoxH")
    widths = get_dataset_data(file_path, group_path + "/boundingBoxW")
    pixel_data = get_dataset_data(file_path, group_path + "/cropPixelData")

    if offsets is None or heights is None or widths is None or pixel_data is None:
        return jsonify("Error: offsets, heights, or widths not found"), 400


    offset = offsets["data"][element_index]
    height = heights["data"][element_index]
    width = widths["data"][element_index]

    crop = np.array(pixel_data["data"][offset:offset + width * height]).reshape(height, width)

    # Objects are stored bright on a dark ground, which is how the sensor
    # sees them; invert so they read as dark on light, the way they look
    # down a microscope.
    img_uint8 = (255 - crop).astype(np.uint8)

    if isolate:
        # A bounding box usually catches parts of its neighbours. The detector
        # stored which pixels belong to *this* object, so everything else can
        # go to white -- the same idea as the python pipeline's isolate_crops,
        # done at display time instead of when the crop was written.
        mask_data = get_dataset_data(file_path, group_path + "/maskPixelData")
        if mask_data is not None:
            mask = np.array(
                mask_data["data"][offset:offset + width * height]
            ).reshape(height, width)
            img_uint8 = np.where(mask > 0, img_uint8, 255).astype(np.uint8)
    
    img = Image.fromarray(img_uint8, mode='L')
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    
    # 5. Encode cleanly to base64
    return base64.b64encode(buf.read()).decode('utf-8')

# The frame is shown at a fixed size; the crops keep their own resolution and
# are fetched one at a time, so this only has to be big enough to aim at.
PREVIEW_SIZE = 800


# Extensions the segmenter accepts as input, in the order we look for them.
_RAW_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff")


def generate_raw_frame(file_path: str, group_name: str):
    """The original frame as the camera recorded it.

    generate_frame_view() reconstructs a frame from the crops the segmenter
    kept, so everything it discarded is white. That is the right picture for
    judging detections and the wrong one for judging the scene the object came
    out of. The raw file is still reachable from the HDF5 alone: the root
    records ``sourcePath`` and every group is named after its source frame.

    Returned at PREVIEW_SIZE, like the reconstruction, so the caller can swap
    one for the other under the same box overlay. Raw frames are already dark
    particles on a light ground, so unlike the crops they need no inversion.
    """
    attrs = get_attributes(file_path, "/")
    if attrs is None or "sourcePath" not in attrs:
        return {"error": "this file records no sourcePath"}

    source = attrs["sourcePath"]
    if isinstance(source, bytes):
        source = source.decode("utf-8", "replace")
    source_dir = Path(str(source))

    stem = group_name.strip("/").split("/")[0]

    # Since the profile-dataset handover the segmenters read
    # profile_dataset.h5 (the file, or the directory holding it), so the raw
    # frame is a row of /frames there rather than an image file.
    dataset = source_dir
    if dataset.is_dir():
        dataset = dataset / "profile_dataset.h5"
    if dataset.suffix == ".h5" and dataset.is_file():
        return _raw_frame_from_dataset(dataset, stem)

    candidate = None
    for ext in _RAW_EXTENSIONS:
        probe = source_dir / (stem + ext)
        if probe.exists():
            candidate = probe
            break
    if candidate is None:
        # The share may be mounted at a different point than when the file was
        # written; say so plainly rather than showing a blank frame.
        return {"error": f"raw frame for '{stem}' not found under {source_dir}"}

    image = cv2.imread(str(candidate), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return {"error": f"could not read {candidate}"}

    return _raw_preview(image, str(candidate))


# A PISCO3 cast holds ~100k frame names; reading them all per click would
# cost more than the frame itself, so the name -> row map is kept per file.
_NAME_INDEX_CACHE_SIZE = 8
_name_index_cache = OrderedDict()
_name_index_lock = threading.Lock()


def _dataset_name_index(dataset: Path, f):
    stat = dataset.stat()
    key = (str(dataset), stat.st_size, stat.st_mtime)
    with _name_index_lock:
        if key in _name_index_cache:
            _name_index_cache.move_to_end(key)
            return _name_index_cache[key]
    names = f["names"].asstr()[:]
    index = {name: i for i, name in enumerate(names)}
    with _name_index_lock:
        _name_index_cache[key] = index
        while len(_name_index_cache) > _NAME_INDEX_CACHE_SIZE:
            _name_index_cache.popitem(last=False)
    return index


def _raw_frame_from_dataset(dataset: Path, stem: str):
    try:
        with h5py.File(dataset, "r") as f:
            row = _dataset_name_index(dataset, f).get(stem)
            if row is None:
                return {"error": f"frame '{stem}' not in {dataset}"}
            image = f["frames"][row]
            bit_depth = int(f.attrs.get("bit_depth", 8))
    except OSError as e:
        return {"error": f"could not read {dataset}: {e}"}

    if image.dtype != np.uint8:
        image = (image >> max(bit_depth - 8, 0)).clip(0, 255).astype(np.uint8)
    return _raw_preview(image, f"{dataset}:/frames[{row}]")


def _raw_preview(image, path: str):
    image = cv2.resize(image, (PREVIEW_SIZE, PREVIEW_SIZE))
    buf = io.BytesIO()
    Image.fromarray(image, mode="L").save(buf, format="PNG")
    buf.seek(0)
    return {
        "image": base64.b64encode(buf.read()).decode("utf-8"),
        "path": path,
        "preview_size": PREVIEW_SIZE,
    }


def generate_full_dataset_image(file_path: str, dataset_path: str):
    """Just the rendered frame, for callers that do not want the geometry."""
    return generate_frame_view(file_path, dataset_path)["image"]


def generate_frame_view(file_path: str, dataset_path: str):
    # 1. get image height and width from attributes
    # 2. get cropPixelData and bounding boxes
    # 3. create empty image and place crops on the image
    # 4. convert image

    attrs = get_attributes(file_path, "/")
    if attrs is None:
        return jsonify("Error: no attributes found"), 400

    image_height = attrs["imageHeight"]
    image_width = attrs["imageWidth"]

    image = np.full((image_height, image_width, 3), 255, dtype=np.uint8)

    group_path = "/".join(dataset_path.split("/")[:-1])
    offsets = get_dataset_data(file_path, group_path + "/offsets")
    xs = get_dataset_data(file_path, group_path + "/boundingBoxX")
    ys = get_dataset_data(file_path, group_path + "/boundingBoxY")
    heights = get_dataset_data(file_path, group_path + "/boundingBoxH")
    widths = get_dataset_data(file_path, group_path + "/boundingBoxW")
    pixel_data = get_dataset_data(file_path, group_path + "/cropPixelData")

    if (offsets is None or heights is None or widths is None or pixel_data is None or xs
        is None or ys is None):
        return jsonify("Error: offsets, heights, or widths not found"), 400


    for i in range(len(offsets["data"])):
        width = widths["data"][i]
        height = heights["data"][i]
        offset = offsets["data"][i]
        x = xs["data"][i]
        y = ys["data"][i]

        crop = np.array(pixel_data["data"][offset:offset+width*height]).reshape(height, width)
        # Inverted here, before the boxes are drawn, so they keep their colour.
        image[y:y+height, x:x+width, :] = (255 - crop).astype(np.uint8)[:, :, None]

        cv2.rectangle(image, (x, y), (x+width, y+height), (0, 255, 0), 2)


    image = cv2.resize(image, (PREVIEW_SIZE, PREVIEW_SIZE))
    image = Image.fromarray(image, mode='RGB')

    buf = io.BytesIO()
    image.save(buf, format='PNG')
    buf.seek(0)

    # Where each object landed in the resized frame, so a click on the preview
    # can be turned back into the index of a crop. Sent as plain arrays --
    # [index, x, y, w, h, full_w, full_h] -- because a busy frame carries a
    # couple of thousand of them.
    # float() because the image size comes from HDF5 attributes as numpy
    # scalars, and those do not always survive being turned into JSON.
    sx = PREVIEW_SIZE / float(image_width)
    sy = PREVIEW_SIZE / float(image_height)
    boxes = []
    for i in range(len(offsets["data"])):
        w = widths["data"][i]
        h = heights["data"][i]
        boxes.append([i,
                      round(xs["data"][i] * sx, 1), round(ys["data"][i] * sy, 1),
                      round(w * sx, 1), round(h * sy, 1),
                      int(w), int(h)])

    return {
        "image": base64.b64encode(buf.read()).decode('utf-8'),
        "boxes": boxes,
        "preview_size": PREVIEW_SIZE,
    }
