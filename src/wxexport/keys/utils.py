import posixpath


def strip_key_metadata(keys):
    """移除 all_keys.json 中以下划线开头的元数据字段，返回新 dict。"""
    return {k: v for k, v in keys.items() if not k.startswith("_")}


def _is_safe_rel_path(path):
    """检查路径不包含 .. 等遍历组件。"""
    return ".." not in posixpath.normpath(path.replace("\\", "/")).split("/")


def get_key_info(keys, rel_path):
    """按相对路径查找数据库密钥。"""
    if not _is_safe_rel_path(rel_path):
        return None
    return keys.get(rel_path)
