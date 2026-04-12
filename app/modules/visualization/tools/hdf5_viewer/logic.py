import h5py
import os

def get_h5_structure(file_path):
    structure = []
    
    def visitor(name, obj):
        item = {
            "name": name.split('/')[-1],
            "full_path": name,
            "type": "Group" if isinstance(obj, h5py.Group) else "Dataset"
        }
        if isinstance(obj, h5py.Dataset):
            item["shape"] = str(obj.shape)
            item["dtype"] = str(obj.dtype)
        structure.append(item)

    try:
        with h5py.File(file_path, 'r') as f:
            f.visititems(visitor)
    except Exception as e:
        return [{"name": "Error", "full_path": str(e), "type": "Error"}]
        
    return structure

def handle_request(filename):
    """The entry point called by the Hub"""
    # data_dir = os.environ.get('DATA_ROOT', './data')
    # full_path = os.path.join(data_dir, filename)
    full_path = "filename"
    
    if not os.path.exists(full_path):
        return None, "File not found"
        
    structure = get_h5_structure(full_path)
    return structure, None
