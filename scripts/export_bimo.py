#!/usr/bin/env python3
"""Bimo-7B-CyberAgent -> RKLLM(w8a8) for RK3588.

来源选择（重要）:
  --source hf    load_huggingface(dir, device='cpu', dtype='float16') -> 可量化
  --source gguf  load_gguf(file)  -> RKLLM 要求 f16 GGUF 必须 do_quantization=False，
                                    只能产出 fp16 模型（~15GB），无法得到 w8a8。

因此 w8a8 必须走 hf 路线。GGUF 路线保留用于产出 fp16 对照模型。
"""
import argparse
import os
import resource
import sys
import time

# 根目录可用环境变量覆盖，默认沿用作者机器的布局
ROOT = os.environ.get("BIMO_ROOT", "/home/dev/ljh/bimo")

# 必须在 import rkllm 之前设置
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("OMP_NUM_THREADS", "64")
os.environ.setdefault("MKL_NUM_THREADS", "64")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from rkllm.api import RKLLM  # noqa: E402


def rss_gb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024


def ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{ts()}] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="hf", choices=["hf", "gguf"])
    ap.add_argument("--model", default=f"{ROOT}/models/Bimo-hf",
                    help="hf: 模型目录; gguf: .gguf 文件路径")
    ap.add_argument("--dtype", default="float16", choices=["float16", "bfloat16", "float32"],
                    help="仅 hf 路线生效")
    ap.add_argument("--data", default=f"{ROOT}/data/data_quant_cyber.json")
    ap.add_argument("--out", default=f"{ROOT}/out/Bimo-7B-CyberAgent_w8a8_rk3588_mc4096.rkllm")
    ap.add_argument("--max-context", type=int, default=4096)
    ap.add_argument("--opt-level", type=int, default=1, choices=[0, 1])
    ap.add_argument("--cores", type=int, default=3, choices=[1, 2, 3])
    ap.add_argument("--qtype", default="w8a8")
    ap.add_argument("--algo", default="normal")
    ap.add_argument("--no-quant", action="store_true", help="不做量化（GGUF f16 必需）")
    ap.add_argument("--load-only", action="store_true")
    a = ap.parse_args()

    if not (a.max_context <= 16384 and a.max_context % 32 == 0):
        sys.exit(f"max_context 非法: {a.max_context} (需 <=16384 且为 32 的倍数)")

    do_quant = not a.no_quant
    if a.source == "gguf":
        if not os.path.isfile(a.model):
            sys.exit(f"GGUF 不存在: {a.model}")
        if do_quant:
            sys.exit("GGUF 路线不支持再量化：f16 GGUF 必须 --no-quant；要 w8a8 请用 --source hf")
        log(f"GGUF = {a.model} ({os.path.getsize(a.model)/1024**3:.2f} GB)")
    else:
        if not os.path.isdir(a.model):
            sys.exit(f"HF 目录不存在: {a.model}")
        missing = [f for f in ("config.json", "tokenizer_config.json")
                   if not os.path.isfile(os.path.join(a.model, f))]
        if missing:
            sys.exit(f"HF 目录缺少文件: {missing}")
        ct = os.path.join(a.model, "chat_template.jinja")
        log(f"HF dir = {a.model}  dtype={a.dtype}  "
            f"chat_template.jinja={'YES' if os.path.isfile(ct) else 'NO'}")
        if not do_quant and not os.path.isfile(a.data):
            pass
        if do_quant and not os.path.isfile(a.data):
            sys.exit(f"校准集不存在: {a.data}")

    llm = RKLLM()

    # ---- 1. 加载 ----
    t0 = time.time()
    if a.source == "gguf":
        ret = llm.load_gguf(model=a.model)
        log(f"load_gguf ret={ret} elapsed={time.time()-t0:.0f}s rss_peak={rss_gb():.2f}GB")
    else:
        ret = llm.load_huggingface(
            model=a.model, model_lora=None, device="cpu", dtype=a.dtype,
            custom_config=None, load_weight=True,
        )
        log(f"load_huggingface ret={ret} elapsed={time.time()-t0:.0f}s "
            f"rss_peak={rss_gb():.2f}GB")
    if ret != 0:
        sys.exit("load failed")

    if a.load_only:
        log("--load-only 指定，跳过 build")
        return 0

    # ---- 2. build ----
    log(f"build: quant={do_quant} dtype={a.qtype} algo={a.algo} opt_level={a.opt_level} "
        f"platform=rk3588 cores={a.cores} max_context={a.max_context} hybrid_rate=0")
    t0 = time.time()
    ret = llm.build(
        do_quantization=do_quant,
        optimization_level=a.opt_level,
        quantized_dtype=a.qtype,
        quantized_algorithm=a.algo,
        target_platform="rk3588",
        num_npu_core=a.cores,
        extra_qparams=None,
        dataset=a.data if do_quant else None,
        hybrid_rate=0,
        max_context=a.max_context,
    )
    log(f"build ret={ret} elapsed={time.time()-t0:.0f}s rss_peak={rss_gb():.2f}GB")
    if ret != 0:
        sys.exit("build failed")

    # ---- 3. export ----
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    t0 = time.time()
    ret = llm.export_rkllm(a.out)
    log(f"export ret={ret} elapsed={time.time()-t0:.0f}s -> {a.out}")
    if ret != 0:
        sys.exit("export failed")

    if not os.path.isfile(a.out):
        sys.exit("export reported success but file missing")
    gb = os.path.getsize(a.out) / 1024**3
    log(f"OUTPUT SIZE = {gb:.2f} GB   rss_peak_total={rss_gb():.2f}GB")
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
