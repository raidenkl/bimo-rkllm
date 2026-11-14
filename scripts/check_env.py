#!/usr/bin/env python3
"""转换前环境预检。

在动手之前跑一遍，把会浪费几小时的问题提前排掉。特别是内存：
rkllm-toolkit 用 CPU 跑 build 时，fp16/bf16 都会被强制降级为 float32，
7B 模型的权重就要 30.5GB；而 `--algo normal` 在优化阶段需要单次申请
约 76.8GB 连续内存。两者叠加，实际门槛在 150GB 以上。

用法:
    python scripts/check_env.py
    python scripts/check_env.py --model-dir /path/to/Bimo-hf --sdk /path/to/rknn-llm

退出码: 0 全部通过或有可忽略警告; 1 存在阻断项
"""
import argparse
import os
import shutil
import sys

# 关键阈值。数值来自实测，不是拍的
RAM_BLOCK_GB = 60      # 低于这个连 algo=none 都跑不动
RAM_OK_GB = 150        # algo=normal 的推荐线
DISK_BLOCK_GB = 60     # 权重 15GB + 产物 9GB + 余量
EXPECT_PY = "3.10"
EXPECT_TOOLKIT_PREFIX = "1.2.3"

errors = []
warns = []
oks = []


def ok(msg):
    oks.append(msg)


def warn(msg):
    warns.append(msg)


def err(msg):
    errors.append(msg)


def check_platform():
    import platform

    machine = platform.machine()
    if machine in ("x86_64", "AMD64"):
        ok(f"架构 {machine}")
    elif machine in ("aarch64", "arm64"):
        err(f"架构 {machine}：rkllm-toolkit 只有 x86_64 的 wheel，不能直接在 ARM 机器上转换")
    else:
        err(f"架构 {machine}：未验证的平台")


def check_python():
    v = "%d.%d" % sys.version_info[:2]
    if v == EXPECT_PY:
        ok(f"Python {sys.version.split()[0]}")
    else:
        err(f"Python {v}：toolkit 的 wheel 按 cp3x 区分，3.10 最稳（现有 wheel 覆盖 3.9-3.12）")


def check_torch():
    try:
        import torch
    except ImportError as e:
        err(f"torch 未安装或导入失败: {e}")
        return
    cuda = torch.cuda.is_available()
    ok(f"torch {torch.__version__}（CUDA 可用: {cuda}）")
    if not cuda:
        warn("无可用 GPU：dtype 会被强制转 float32，内存占用翻倍，build 时间也长。"
             "有 GPU 时 fp16 能把峰值内存砍掉一半")


def check_deps():
    for mod, attr in (("transformers", "__version__"), ("numpy", "__version__"),
                      ("safetensors", "__version__"), ("sentencepiece", "__version__")):
        try:
            m = __import__(mod)
            ok(f"{mod} {getattr(m, attr, '?')}")
        except ImportError:
            err(f"{mod} 未安装")


def check_toolkit():
    try:
        from rkllm.api import RKLLM  # noqa: F401
    except ImportError as e:
        err(f"rkllm-toolkit 未安装或导入失败: {e}"
            "（它不在 PyPI 上，需装 SDK 自带的 wheel）")
        return
    try:
        import rkllm
        ver = getattr(rkllm, "__version__", None) or "unknown"
    except Exception:
        ver = "unknown"

    # 用 build 签名判断版本能力，比版本号更可靠
    import inspect
    sig = str(inspect.signature(RKLLM.build))
    if "max_context" not in sig:
        err(f"build() 签名里没有 max_context，toolkit 版本可能过老: {sig}")
    else:
        ok(f"rkllm-toolkit 可用，build 签名正常")

    if EXPECT_TOOLKIT_PREFIX not in str(ver) and ver != "unknown":
        warn(f"toolkit 版本 {ver}，本次验证用的是 {EXPECT_TOOLKIT_PREFIX}。"
             "跨小版本可能有 API 差异，注意 build 参数与产物兼容性")


def check_memory():
    try:
        with open("/proc/meminfo") as f:
            info = dict(l.split(":", 1) for l in f if ":" in l)
        total_gb = int(info["MemTotal"].split()[0]) / 1024 / 1024
        avail_gb = int(info["MemAvailable"].split()[0]) / 1024 / 1024
    except Exception as e:
        warn(f"读取 /proc/meminfo 失败: {e}")
        return

    swap_gb = 0.0
    try:
        with open("/proc/swaps") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) > 2:
                    swap_gb += int(parts[2]) / 1024 / 1024
    except Exception:
        pass

    line = f"内存 {total_gb:.0f}GB（可用 {avail_gb:.0f}GB，swap {swap_gb:.0f}GB）"
    if total_gb < RAM_BLOCK_GB:
        err(line + f"：低于 {RAM_BLOCK_GB}GB 底线，加载阶段就会 OOM")
    elif total_gb < RAM_OK_GB:
        warn(line + f"：够跑 --algo none（跳过校准优化），但 --algo normal 需要 "
                    f"{RAM_OK_GB}GB 以上（要单次申请约 76.8GB 连续内存）。"
                    "想拿到带校准的量化结果，需要更大的机器")
    else:
        ok(line + "：满足 --algo normal")


def check_disk(path="."):
    try:
        free_gb = shutil.disk_usage(path).free / 1024**3
    except Exception as e:
        warn(f"磁盘检查失败: {e}")
        return
    if free_gb < DISK_BLOCK_GB:
        err(f"磁盘可用 {free_gb:.0f}GB：低于 {DISK_BLOCK_GB}GB，"
            "权重 15GB + 产物 9GB + 临时文件恐怕放不下")
    else:
        ok(f"磁盘可用 {free_gb:.0f}GB")


def check_model_dir(path):
    if not path:
        return
    if not os.path.isdir(path):
        err(f"模型目录不存在: {path}")
        return
    need = ["config.json", "tokenizer_config.json", "tokenizer.json"]
    missing = [f for f in need if not os.path.isfile(os.path.join(path, f))]
    if missing:
        err(f"模型目录缺文件: {missing}")
    else:
        ok("模型目录结构完整")

    shards = [f for f in os.listdir(path) if f.endswith(".safetensors")]
    if not shards:
        err("模型目录没有 .safetensors 分片")
    else:
        total = sum(os.path.getsize(os.path.join(path, f)) for f in shards)
        ok(f"{len(shards)} 个分片，合计 {total/1024**3:.2f}GiB")
        if total / 1024**3 < 14:
            err(f"分片总体积只有 {total/1024**3:.2f}GiB，疑似下载不完整")

    # transformers v5 格式的两个坑
    import json
    cfg = json.load(open(os.path.join(path, "config.json")))
    if "rope_parameters" in cfg:
        err("config.json 用的是 transformers v5 字段 rope_parameters，"
            "v4 环境会静默丢失 YaRN。先跑 scripts/patch_hf_config.py")
    tc = json.load(open(os.path.join(path, "tokenizer_config.json")))
    if isinstance(tc.get("extra_special_tokens"), list):
        err("tokenizer_config.json 的 extra_special_tokens 是 list，"
            "v4 会报 'list' object has no attribute 'keys'。先跑 scripts/patch_hf_config.py")


def check_sdk(path):
    if not path:
        return
    wheels = os.path.join(path, "rkllm-toolkit", "packages")
    if not os.path.isdir(wheels):
        err(f"SDK 目录不对，找不到 {wheels}")
        return
    found = [f for f in os.listdir(wheels) if f.startswith("rkllm_toolkit") and f.endswith(".whl")]
    if found:
        ok(f"SDK 里有 {len(found)} 个 toolkit wheel: {sorted(found)[0]} ...")
    else:
        err(f"{wheels} 下没有 rkllm_toolkit 的 wheel")
    rt = os.path.join(path, "rkllm-runtime", "Linux", "librkllm_api", "aarch64", "librkllmrt.so")
    if os.path.isfile(rt):
        ok(f"aarch64 运行时存在（{os.path.getsize(rt)/1024**2:.1f}MB），板端要用这一份")
    else:
        warn(f"未找到 {rt}，板端将没有匹配版本的 librkllmrt.so")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default="", help="HF 权重目录，用于校验完整性")
    ap.add_argument("--sdk", default="", help="rknn-llm SDK 根目录")
    a = ap.parse_args()

    check_platform()
    check_python()
    check_torch()
    check_deps()
    check_toolkit()
    check_memory()
    check_disk()
    check_model_dir(a.model_dir)
    check_sdk(a.sdk)

    print("=" * 62)
    for m in oks:
        print(f"  [OK]   {m}")
    for m in warns:
        print(f"  [WARN] {m}")
    for m in errors:
        print(f"  [FAIL] {m}")
    print("=" * 62)

    if errors:
        print(f"\n{len(errors)} 项阻断，先修掉再开始转换。")
        return 1
    if warns:
        print(f"\n{len(warns)} 项警告，可以继续，但留意上面的说明。")
    else:
        print("\n全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
