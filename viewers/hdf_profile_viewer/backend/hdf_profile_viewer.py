import h5py
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


def profile_counts(file_path: str):
    """Object count per frame, with the depth each frame was taken at.

    This is the profile as a whole rather than one frame of it: how many
    particles were found at each depth, which is what the plot beside the
    preview draws.
    """
    points = []
    with h5py.File(file_path, "r") as f:
        for name in f.keys():
            group = f[name]
            if not isinstance(group, h5py.Group) or "boundingBoxW" not in group:
                continue

            match = _DEPTH_IN_NAME.search(name)
            points.append({
                "name": name,
                "depth": float(match.group(1)) if match else None,
                "count": int(group["boundingBoxW"].shape[0]),
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


def build_tree(node):

    result = {
        "name": node.name,
        "type": "group",
        "children": []
    }

    for key, item in node.items():

        if isinstance(item, h5py.Group):

            result["children"].append(
                build_tree(item)
            )

        else:

            result["children"].append({
                "name": item.name,
                "type": "dataset"
            })

    return result

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

    image = cv2.resize(image, (PREVIEW_SIZE, PREVIEW_SIZE))
    buf = io.BytesIO()
    Image.fromarray(image, mode="L").save(buf, format="PNG")
    buf.seek(0)
    return {
        "image": base64.b64encode(buf.read()).decode("utf-8"),
        "path": str(candidate),
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
