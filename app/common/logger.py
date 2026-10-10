"""简化日志配置：全局 logger，输出到标准输出。"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger("rag_kb")
