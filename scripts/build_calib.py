#!/usr/bin/env python3
"""把 seeds.jsonl 渲染成 RKLLM 量化校准集 data_quant.json。

输入 seeds.jsonl: 每行 {"system": str|null, "user": str, "assistant": str, "domain": str}
输出 JSON 数组:   [{"input": "<已含 ChatML 模板 token>", "target": "..."}]

关键：RKLLM toolkit 只做 tokenize，不拼 chat 模板，所以 input 必须已包含
完整的 <|im_start|>system ... <|im_start|>assistant\\n 前缀，且与 GGUF 内嵌
chat_template 完全一致（含 Bimo 默认 system prompt）。
"""
import argparse
import json
import os
import sys
from collections import Counter

BIMO_SYS = (
    "You are Bimo, an autonomous cybersecurity and systems intelligence agent created by "
    "Saim Azam. You specialize in ethical security auditing, vulnerability research, native "
    "JSON function calling, and multi-turn tool execution. When asked about your identity, "
    "always introduce yourself as Bimo."
)


def render_chatml(system, user, assistant):
    """与 GGUF tokenizer.chat_template 逐字符对齐。"""
    sys_text = system if system else BIMO_SYS
    prompt = (
        f"<|im_start|>system\n{sys_text}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )
    return prompt, assistant


def main():
    ap = argparse.ArgumentParser()
    root = os.environ.get("BIMO_ROOT", "/home/dev/ljh/bimo")
    ap.add_argument("--seeds", default=f"{root}/data/seeds.jsonl")
    ap.add_argument("--tok", default=f"{root}/tok")
    ap.add_argument("--out", default=f"{root}/data/data_quant_cyber.json")
    ap.add_argument("--min-tok", type=int, default=48)
    ap.add_argument("--max-tok", type=int, default=1600)
    ap.add_argument("--report", default=f"{root}/logs/calib_report.txt")
    a = ap.parse_args()

    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(a.tok, trust_remote_code=True)

    rows, dropped, domains = [], 0, Counter()
    total = 0
    for lineno, line in enumerate(open(a.seeds, encoding="utf-8"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        total += 1
        try:
            r = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"[skip] line {lineno} bad json: {e}", file=sys.stderr)
            dropped += 1
            continue

        prompt, target = render_chatml(r.get("system"), r["user"], r.get("assistant", ""))
        n = len(tok(prompt, add_special_tokens=False)["input_ids"])
        if n < a.min_tok or n > a.max_tok:
            print(f"[drop] line {lineno} len={n} domain={r.get('domain','?')}", file=sys.stderr)
            dropped += 1
            continue
        rows.append({"input": prompt, "target": target})
        domains[r.get("domain", "unknown")] += 1

    if not rows:
        print("ERROR: 0 samples kept", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)

    lens = sorted(
        len(tok(x["input"], add_special_tokens=False)["input_ids"]) for x in rows
    )
    p = lambda q: lens[min(len(lens) - 1, int(len(lens) * q))]

    buckets = {"short(<=256)": 0, "mid(257-768)": 0, "long(769-1024)": 0, "xlong(>1024)": 0}
    for n in lens:
        if n <= 256:
            buckets["short(<=256)"] += 1
        elif n <= 768:
            buckets["mid(257-768)"] += 1
        elif n <= 1024:
            buckets["long(769-1024)"] += 1
        else:
            buckets["xlong(>1024)"] += 1

    lines = [
        f"seeds_total = {total}",
        f"kept        = {len(rows)}",
        f"dropped     = {dropped}",
        f"tokens p50  = {p(0.50)}",
        f"tokens p90  = {p(0.90)}",
        f"tokens max  = {lens[-1]}",
        f"tokens sum  = {sum(lens)}",
        "length buckets: " + ", ".join(f"{k}={v}" for k, v in buckets.items()),
        "domains: " + ", ".join(f"{k}={v}" for k, v in sorted(domains.items())),
        f"output      = {a.out}",
        f"sample head = {rows[0]['input'][:90]!r}",
    ]
    report = "\n".join(lines)
    print(report)
    os.makedirs(os.path.dirname(a.report), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
