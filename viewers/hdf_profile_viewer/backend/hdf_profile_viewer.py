from turtle import width
import h5py
import numpy as np
import io
import base64
import numpy as np
from flask import jsonify
from PIL import Image
import cv2


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


def generate_crop(file_path: str, element_index: int, dataset_path: str):
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


    img_uint8 = crop.astype(np.uint8)
    
    img = Image.fromarray(img_uint8, mode='L')
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    
    # 5. Encode cleanly to base64
    return base64.b64encode(buf.read()).decode('utf-8')

def generate_full_dataset_image(file_path: str, dataset_path: str):
    # 1. get image height and width from attributes
    # 2. get cropPixelData and bounding boxes
    # 3. create empty image and place crops on the image
    # 4. convert image

    attrs = get_attributes(file_path, "/")
    if attrs is None:
        return jsonify("Error: no attributes found"), 400

    image_height = attrs["imageHeight"]
    image_width = attrs["imageWidth"]

    image = np.zeros((image_height, image_width, 3), dtype=np.uint8)

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

        image[y:y+height, x:x+width, 0] = np.array(pixel_data["data"][offset:offset+width*height]).reshape(height, width)
        image[y:y+height, x:x+width, 1] = np.array(pixel_data["data"][offset:offset+width*height]).reshape(height, width)
        image[y:y+height, x:x+width, 2] = np.array(pixel_data["data"][offset:offset+width*height]).reshape(height, width)

        cv2.rectangle(image, (x, y), (x+width, y+height), (0, 255, 0), 2)


    image = cv2.resize(image, (800, 800))
    image = Image.fromarray(image, mode='RGB')
    
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    buf.seek(0)
    
    # 5. Encode cleanly to base64
    return base64.b64encode(buf.read()).decode('utf-8')
