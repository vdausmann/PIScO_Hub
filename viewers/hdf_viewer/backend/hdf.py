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


def get_dataset_data(file_path: str, dataset_path: str, frame_idx):
    with h5py.File(file_path, "r") as f:

        ds = f[dataset_path]

        if not isinstance(ds, h5py.Dataset):
            return None

        ndim = ds.ndim
        shape = list(ds.shape)
        
        if ndim >  1:
            if ndim == 2:
                # 2D dataset: shape (height, width)
                img_data = ds[:, :]
            elif ndim == 3:
                # 3D dataset: shape (frames, height, width), take the first frame
                idx = max(0, min(frame_idx, shape[0] - 1))
                img_data = ds[idx, :, :]
            else:
                raise ValueError(f"Unsupported dataset dimensions: {ndim}D (shape: {ds.shape})")
            
            # Handle data types and normalization for browser rendering
            if img_data.dtype != np.uint8:
                img_min = img_data.min()
                img_max = img_data.max()
                if img_max > img_min:
                    # Scale floats or uint16 to 0-255 uint8 range
                    img_normalized = ((img_data - img_min) / (img_max - img_min) * 255).astype(np.uint8)
                else:
                    img_normalized = np.zeros_like(img_data, dtype=np.uint8)
                img_data = img_normalized

            # Convert NumPy array to a Pillow Image
            img = Image.fromarray(img_data)

            max_dimension = 512
            if img.width > max_dimension or img.height > max_dimension:
                    img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            
            # Save image to an in-memory byte buffer as PNG
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
        
            info = {
                "shape": list(ds.shape),
                "data": [],
                "image": base64.b64encode(buf.read()).decode('utf-8'),
                "current_frame": frame_idx if ds.ndim == 3 else 0
            }
        else:
            if ds.size > 100:
                data = ds[:100]
            else:
                data = ds[:]
            info = {
                "shape": list(ds.shape),
                "data": data.tolist(),
                "image": None,
                "current_frame": None
            }

        return info
