from __future__ import annotations

import argparse
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from myinvestetf.config import DB_PATH
from myinvestetf.db import claim_next_queue_item, connect, list_queue, next_queue_item


def _row_value(row: object, key: str, default: object = "") -> object:
    try:
        value = row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        value = row.get(key, default) if isinstance(row, dict) else default
    return default if value is None else value


def format_queue_prompt(row: object) -> str:
    prompt = str(_row_value(row, "prompt"))
    metadata = [
        str(_row_value(row, "task_keyword")),
        "",
        "队列任务元数据：",
        f"- report_id：{_row_value(row, 'report_id')}",
        f"- code：{_row_value(row, 'code')}",
        f"- name：{_row_value(row, 'name')}",
        f"- task_type：{_row_value(row, 'task_type')}",
        f"- task_id：{_row_value(row, 'task_id')}",
        f"- run_id：{_row_value(row, 'run_id')}",
        f"- priority：{_row_value(row, 'priority')}",
        f"- stage：{_row_value(row, 'stage')}",
        f"- depends_on_task_type：{_row_value(row, 'depends_on_task_type')}",
        f"- source_type：{_row_value(row, 'source_type')}",
        f"- source_detail：{_row_value(row, 'source_detail')}",
        "",
        "Codex 执行边界：",
        "- 只执行本队列任务元数据对应的一只 ETF、一个 task_type。",
        "- 不从上游列表扩展新 ETF，不合并处理其他队列项。",
        "- 成功导入报告后，确认对应 task_queue 状态进入 DONE；失败时进入 FAILED 或 BLOCKED，并写明原因。",
        "- 不输出交易指令、现金金额或份额数量。",
        "",
        "队列任务提示词：",
        prompt,
    ]
    return "\n".join(metadata)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate one Codex prompt for one etf only.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--next", action="store_true", help="Use the next pending queue item.")
    group.add_argument("--code", help="Use a specific etf code from the queue.")
    parser.add_argument("--task-type", choices=["profile", "valuation"], help="Limit --code to one task type.")
    parser.add_argument("--claim", action="store_true", help="Mark the selected --next task as in_progress.")
    args = parser.parse_args()
    if args.claim and not args.next:
        parser.error("--claim can only be used with --next")

    with closing(connect(DB_PATH)) as conn:
        if args.next:
            row = claim_next_queue_item(conn) if args.claim else next_queue_item(conn)
        else:
            matches = [
                item
                for item in list_queue(conn)
                if item["code"].upper() == args.code.upper()
                and (args.task_type is None or item["task_type"] == args.task_type)
            ]
            row = matches[0] if matches else None
    if row is None:
        print("没有找到可领取的待研究ETF。请先运行 python scripts/ingest_index.py，或等待前置产品结构深研完成。")
        return 1
    print(format_queue_prompt(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
