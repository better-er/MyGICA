"""pytest 全局配置"""

import pytest
from loguru import logger


@pytest.fixture(autouse=True)
def quiet_logger():
    """配置解析的补全逻辑会打大量警告，测试期间屏蔽，结束后恢复"""
    logger.disable("MyGICA")
    yield
    logger.enable("MyGICA")
