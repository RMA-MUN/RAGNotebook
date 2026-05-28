import logging
import os
from datetime import datetime

# 获取项目根目录
project_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))

# 如果没有logs文件夹，则创建
logs_dir = os.path.join(project_path, 'logs')
os.makedirs(logs_dir, exist_ok=True)

# 日志模式
DEFAULT_LOGGING_FORMAT = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def get_logger(
        name: str = "agent",
        console_level: int = logging.INFO,
        file_level: int = logging.DEBUG,
        log_file: str = None,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 避免重复添加处理器
    if logger.handlers:
        return logger

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(DEFAULT_LOGGING_FORMAT)
    logger.addHandler(console_handler)

    # 文件处理器
    # 如果没有指定log_file，使用默认名称
    if log_file is None:
        log_file = f"{name}_{datetime.now().strftime('%Y%m%d')}.log"

    # 确保logs目录存在
    logs_dir = os.path.join(project_path, 'logs')
    os.makedirs(logs_dir, exist_ok=True)

    file_handler = logging.FileHandler(os.path.join(logs_dir, log_file), encoding='utf-8')
    file_handler.setLevel(file_level)
    file_handler.setFormatter(DEFAULT_LOGGING_FORMAT)
    logger.addHandler(file_handler)

    return logger


logger = get_logger()


if __name__ == '__main__':
    # 测试创建日志文件
    logger = get_logger(log_file='test.log')
    print(f"项目根目录: {project_path}")
    print(f"日志目录: {logs_dir}")
    logger.info('这是一条info日志')
    logger.debug('这是一条debug日志')
    logger.error('这是一条error日志')
    logger.warning('这是一条warning日志')
    print("日志测试完成，请检查logs目录是否创建")
