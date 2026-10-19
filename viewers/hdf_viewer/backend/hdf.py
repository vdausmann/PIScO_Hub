import hdf5plugin
import h5py
import os
import base64
import io
import numpy as np
from PIL import Image

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
        if not group_path:
            group_attrs = get_attributes(file_path, "/")
        else:
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


def _frames_in_use(f, ds):
    """How many rows of a frame series hold data.

    The Pisco Video Pipeline preallocates its arrays (20000 rows) and
    records the true count as the file's n_frames; the rows after it are
    zeros, not frames.
    """
    n = ds.shape[0]
    if "n_frames" in f.attrs and ds.parent.name == "/":
        try:
            n = min(n, int(f.attrs["n_frames"]))
        except (TypeError, ValueError):
            pass
    return max(n, 1)


def _is_encoded_series(ds):
    """A 1-D dataset of variable-length bytes: one encoded image (JPEG)
    per row, as the recorder's h5jpeg sink stores them."""
    return ds.ndim == 1 and (h5py.check_vlen_dtype(ds.dtype) is not None
                             or ds.dtype.kind in ("O", "S", "V"))


def _attr_text(value):
    if isinstance(value, (bytes, np.bytes_)):
        return value.decode(errors="replace")
    return str(value)


def get_dataset_data(file_path: str, dataset_path: str, frame_idx):
    """One image of a dataset for the browser, or its first values.

    Images: a 2-D array; a stack of grey frames (frames, h, w); a stack of
    colour frames (frames, h, w, 3) - BGR when the recorder says so - and
    a series of encoded images, one JPEG per row. Frame series report
    their shape as (frames, h, w) so the page's frame navigation works the
    same for all of them.
    """
    with h5py.File(file_path, "r") as f:

        ds = f[dataset_path]

        if not isinstance(ds, h5py.Dataset):
            return None

        shape = list(ds.shape)
        img = None
        frames = None

        if _is_encoded_series(ds) and ds.shape[0] > 0:
            frames = _frames_in_use(f, ds)
            idx = max(0, min(frame_idx, frames - 1))
            raw = ds[idx]
            data = raw.tobytes() if isinstance(raw, np.ndarray) else bytes(raw)
            try:
                img = Image.open(io.BytesIO(data))
                img.load()
            except Exception:
                img = None          # not an image after all: show values below
                frames = None

        elif ds.ndim > 1:
            if ds.ndim == 2:
                img_data = ds[:, :]
            elif ds.ndim == 3:
                frames = _frames_in_use(f, ds)
                idx = max(0, min(frame_idx, frames - 1))
                img_data = ds[idx, :, :]
            elif ds.ndim == 4 and shape[-1] in (3, 4):
                frames = _frames_in_use(f, ds)
                idx = max(0, min(frame_idx, frames - 1))
                img_data = ds[idx]
                if _attr_text(f.attrs.get("pixel_format", "")).upper().startswith("BGR"):
                    img_data = img_data[..., [2, 1, 0] + ([3] if shape[-1] == 4 else [])]
            else:
                raise ValueError(f"Unsupported dataset dimensions: {ds.ndim}D (shape: {ds.shape})")

            # Handle data types and normalization for browser rendering
            if img_data.dtype != np.uint8:
                img_min = img_data.min()
                img_max = img_data.max()
                if img_max > img_min:
                    # Scale floats or uint16 to 0-255 uint8 range
                    img_data = ((img_data - img_min) / (img_max - img_min) * 255).astype(np.uint8)
                else:
                    img_data = np.zeros_like(img_data, dtype=np.uint8)

            img = Image.fromarray(img_data)

        if img is not None:
            max_dimension = 512
            if img.width > max_dimension or img.height > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

            # Save image to an in-memory byte buffer as PNG
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)

            if frames is not None:
                shape = [frames, img.height, img.width]
            return {
                "shape": shape,
                "data": [],
                "image": base64.b64encode(buf.read()).decode('utf-8'),
                "current_frame": idx if frames is not None else 0
            }

        data = ds[:100] if ds.size > 100 else ds[:]
        return {
            "shape": shape,
            "data": [_attr_text(v) if isinstance(v, (bytes, np.bytes_, np.ndarray)) else v
                     for v in np.asarray(data, dtype=object).ravel().tolist()]
                    if ds.dtype.kind in ("O", "S", "V") else data.tolist(),
            "image": None,
            "current_frame": None
        }
