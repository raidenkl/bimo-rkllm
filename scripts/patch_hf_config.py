#!/usr/bin/env python3
"""把 transformers v5 格式的模型配置改回 v4 能吃的格式。

Bimo-7B-CyberAgent 这个仓库是 v5 格式发布的（transformers_version 5.22.0），
而 rkllm-toolkit 依赖的是 4.x，直接加载会踩两个坑：

1. tokenizer_config.json 的 extra_special_tokens 是 list，v4 当 dict 用，
   报 `'list' object has no attribute 'keys'`。
2. config.json 用 rope_parameters 描述 YaRN，v4 只认 rope_scaling，
   结果是静默丢失 YaRN，8K 以上的长上下文失效（这个更隐蔽，不报错）。

脚本可重复执行，第二次跑会检测到已修补并直接跳过。原文件备份为 *.v5bak。

用法:
    python scripts/patch_hf_config.py --model-dir /path/to/Bimo-hf
    python scripts/patch_hf_config.py --model-dir ... --dry-run
"""
import argparse
import json
import os
import shutil
import sys


def patch_config(path, dry_run):
    """config.json: rope_parameters -> rope_scaling，去掉 v5 专有字段。"""
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    changed = []

    rp = cfg.pop("rope_parameters", None)
    if rp:
        cfg["rope_scaling"] = {
            "rope_type": rp.get("rope_type", rp.get("type", "yarn")),
            "factor": rp.get("factor", 4.0),
            "original_max_position_embeddings": rp.get(
                "original_max_position_embeddings", 8192),
        }
        cfg["rope_theta"] = rp.get("rope_theta", 10000.0)
        changed.append(f"rope_parameters -> rope_scaling (factor={cfg['rope_scaling']['factor']}, "
                       f"orig_ctx={cfg['rope_scaling']['original_max_position_embeddings']})")

    if "layer_types" in cfg:
        n = len(cfg.pop("layer_types"))
        changed.append(f"删除 layer_types（v5 专有，{n} 项）")

    # v5 用 dtype，v4 用 torch_dtype
    if "dtype" in cfg and "torch_dtype" not in cfg:
        cfg["torch_dtype"] = cfg["dtype"]
        changed.append(f"dtype {cfg['dtype']} -> torch_dtype")

    if not changed:
        return False, []

    if not dry_run:
        shutil.copy2(path, path + ".v5bak") if not os.path.exists(path + ".v5bak") else None
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    return True, changed


def patch_tokenizer_config(path, dry_run):
    """tokenizer_config.json: 丢掉 list 形态的 extra_special_tokens。"""
    with open(path, encoding="utf-8") as f:
        tc = json.load(f)

    est = tc.get("extra_special_tokens")
    if not isinstance(est, list):
        return False, []

    if not dry_run:
        tc.pop("extra_special_tokens")
        shutil.copy2(path, path + ".v5bak") if not os.path.exists(path + ".v5bak") else None
        with open(path, "w", encoding="utf-8") as f:
            json.dump(tc, f, indent=2, ensure_ascii=False)
    return True, [f"删除 extra_special_tokens（list，{len(est)} 项）"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--dry-run", action="store_true", help="只报告要改什么，不落盘")
    a = ap.parse_args()

    if not os.path.isdir(a.model_dir):
        sys.exit(f"目录不存在: {a.model_dir}")

    targets = [
        ("config.json", patch_config),
        ("tokenizer_config.json", patch_tokenizer_config),
    ]

    total = 0
    for name, fn in targets:
        p = os.path.join(a.model_dir, name)
        if not os.path.isfile(p):
            print(f"[skip] {name} 不存在")
            continue
        changed, notes = fn(p, a.dry_run)
        if changed:
            total += 1
            tag = "[dry-run] 待修改" if a.dry_run else "[done] 已修改"
            print(f"{tag} {name}")
            for n in notes:
                print(f"         - {n}")
        else:
            print(f"[skip] {name} 已是 v4 格式")

    if total == 0:
        print("\n无需改动。")
    elif a.dry_run:
        print("\n以上为预演结果，去掉 --dry-run 才会真正写入。")
    else:
        print("\n改完了。原文件备份为 *.v5bak，验证方式：")
        print("  python -c \"from rkllm.api import RKLLM; "
              "print(RKLLM().load_huggingface(model='%s', device='cpu', "
              "dtype='float32', load_weight=False))\"" % a.model_dir)
        print("返回 0 说明配置解析通过（不加载权重，几秒完成）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
