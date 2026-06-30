import h5py

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


def get_dataset_info(file_path: str, dataset_path: str):
    with h5py.File(file_path, "r") as f:

        ds = f[dataset_path]

        if not isinstance(ds, h5py.Dataset):
            return None

        info = {
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
