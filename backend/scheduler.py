"""
TrendArticle · 定时任务调度器（v5.0）

功能：
  - 定期健康检查：每 5 分钟执行一次数据源健康检查
  - 性能日志：每 10 分钟记录一次系统性能
  - 错误日志：记录所有抓取失败的详细信息

用法：
  python backend/scheduler.py        # 前台运行
  python backend/scheduler.py --daemon  # 后台守护进程
"""

import time
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# 添加 backend 目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetchers

logger = logging.getLogger(__name__)

# 日志目录
LOG_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / 'logs'
LOG_DIR.mkdir(exist_ok=True)

# 错误日志文件
ERROR_LOG = LOG_DIR / 'errors.log'
HEALTH_LOG = LOG_DIR / 'health.log'
PERF_LOG = LOG_DIR / 'performance.log'


def setup_logging():
    """设置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[
            logging.FileHandler(LOG_DIR / 'scheduler.log'),
            logging.StreamHandler(sys.stdout),
        ]
    )


def write_log_file(filepath: Path, message: str):
    """追加写入日志文件"""
    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now().isoformat()} {message}\n")


def health_check_cycle():
    """执行一轮健康检查"""
    logger.info("Starting health check cycle...")

    # 执行一次完整抓取
    results = fetchers.fetch_all()

    # 统计
    total = len(results)
    active = sum(1 for v in results.values() if len(v) > 0)
    failed = {k: v for k, v in results.items() if len(v) == 0}

    # 写入健康日志
    health_msg = f"Cycle: total={total}, active={active}, failed={len(failed)}"
    if failed:
        health_msg += f". Failed sources: {list(failed.keys())}"
    write_log_file(HEALTH_LOG, health_msg)

    # 写入错误日志
    if failed:
        for key, items in failed.items():
            error_msg = fetchers.SOURCE_HEALTH.get(key, {}).get('last_error', 'Unknown error')
            write_log_file(ERROR_LOG, f"Source '{key}' failed: {error_msg} | Items: {len(items)}")

    # 写入性能日志
    health_report = fetchers.get_health_report()
    perf_data = {
        'timestamp': datetime.now().isoformat(),
        'total_sources': total,
        'active_sources': active,
        'failed_sources': len(failed),
        'sources': {k: {'status': v['status'], 'success_rate': v['success_rate'], 'error': v['last_error'][:100]}
                     for k, v in health_report.items()},
    }
    write_log_file(PERF_LOG, json.dumps(perf_data, ensure_ascii=False))

    logger.info(f"Health check complete: {active}/{total} sources active")


def run_scheduler(interval: int = 300, daemon: bool = False):
    """运行调度器
    
    Args:
        interval: 检查间隔（秒），默认 5 分钟
        daemon: 是否作为守护进程运行
    """
    setup_logging()

    logger.info(f"TrendArticle Scheduler v5.0 starting (interval={interval}s)")
    logger.info(f"Log directory: {LOG_DIR}")

    # 首次立即执行
    health_check_cycle()

    # 定时执行
    try:
        while True:
            time.sleep(interval)
            health_check_cycle()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")
    except Exception as e:
        logger.error(f"Scheduler error: {e}")
        write_log_file(ERROR_LOG, f"Scheduler crashed: {e}")
        raise


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='TrendArticle Scheduler')
    parser.add_argument('--interval', type=int, default=300, help='Check interval in seconds (default: 300)')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    args = parser.parse_args()

    if args.once:
        health_check_cycle()
    else:
        run_scheduler(interval=args.interval, daemon=args.daemon)
