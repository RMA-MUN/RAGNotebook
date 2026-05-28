import os


def get_project_root() -> str:
    """
    获取项目根目录
    :return: 项目根目录路径
    """
    # 从当前文件所在目录向上两级（app/utils -> app -> backend）
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(current_dir)
    project_root = os.path.dirname(app_dir)
    return project_root

def get_abstract_path(relative_path: str) -> str:
    """
    根据传入的相对路径，获取项目根目录下的绝对路径
    :param relative_path: 相对项目根目录的路径
    :return: 绝对路径
    """
    project_path = get_project_root()
    # 确保路径格式正确，处理不同操作系统的路径分隔符
    abstract_path = os.path.normpath(os.path.join(project_path, relative_path))
    return abstract_path

def get_data_path() -> str:
    """
    获取数据目录路径
    :return: 数据目录绝对路径
    """
    return get_abstract_path('data')

def get_config_path() -> str:
    """
    获取配置目录路径
    :return: 配置目录绝对路径
    """
    return get_abstract_path('app/config')


if __name__ == '__main__':
    print(f"项目根目录: {get_project_root()}")
    print(f"数据目录: {get_data_path()}")
    print(f"配置目录: {get_config_path()}")
