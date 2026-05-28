import yaml


def load_config(
        config_path: str,
        encoding: str = 'utf-8'
) -> dict:
    with open(config_path, encoding=encoding) as file:
        config = yaml.load(file, Loader=yaml.FullLoader)
    return config


