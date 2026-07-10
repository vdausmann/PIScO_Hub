import h5py
import os

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
