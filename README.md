# Bimo-7B-CyberAgent 转 RKLLM 并在 RK3588 上运行

把 [Bimo-7B-CyberAgent](https://huggingface.co/iBimo-o/Bimo-7B-CyberAgent)（Qwen2.5-Coder-7B-Instruct 的安全领域微调版）转成 RKLLM 的 w8a8 格式，跑在 RK3588 的 NPU 上。

仓库里是自己写的转换脚本、板端部署脚本、量化校准数据和一份完整的踩坑记录。模型权重和厂商二进制都不在这里。

## 先说结论

7.6B 模型在 RK3588 上只有 w8a8 一条路，产物约 7.65GiB，运行峰值内存 7.17GB，16GB 板能跑，生成速度 3 tok/s 左右。

如果你的板子是 8GB 内存，这个模型装不下，不用往下看了。

转换端的要求比板端苛刻得多：CPU 机器需要有 **150GB 以上内存**才能跑带校准的量化（`--algo normal`）。没有的话只能退到 `--algo none`，质量会有可见的下降，详见文末。

## 这个仓库不包含什么

| 内容 | 体积 | 怎么获取 |
| --- | --- | --- |
| 模型权重 | 15.2GB | `hf download iBimo-o/Bimo-7B-CyberAgent`，或用 `hf-mirror.com` 镜像 |
| `.rkllm` 产物 | 8.2GB | 按本文步骤自己转 |
| `rkllm_toolkit-*.whl` | 7MB | 随 [RKLLM SDK](https://github.com/airockchip/rknn-llm) 发布 |
| `librkllmrt.so` | 7.5MB | 同上，在 `rkllm-runtime/Linux/librkllm_api/aarch64/` |
| `llm_demo.cpp` / `rkllm.h` | 20KB | 同上，在 `examples/rkllm_api_demo/` |

厂商二进制不重新分发，请从官方获取。版本要认准 **1.2.3**，板端 runtime 和转换工具链必须是同一个版本。

## 目录结构

```
.
├── scripts/
│   ├── check_env.py          转换前预检，把会浪费几小时的问题提前排掉
│   ├── patch_hf_config.py    把 transformers v5 格式的配置改回 v4
│   ├── dump_gguf_meta.py     解析 GGUF 头部元数据
│   ├── build_calib.py        把 seeds 渲染成量化校准集
│   ├── make_long_seeds.py    生成中长上下文的校准样本
│   └── export_bimo.py        load → build → export 主流程
├── data/
│   ├── seeds.jsonl           187 条安全域种子样本（六类任务）
│   └── data_quant_cyber.json 渲染好的校准集，32,316 token
├── deploy/
│   ├── push_to_board.sh      推模型和运行时到板卡（rsync 或 adb）
│   └── smoke_test.md         板端部署与验证手册，含实测数据
├── docs/
│   └── Bimo-7B-RK3588-部署实录.md   完整过程记录，含所有报错原文
├── requirements-lock.txt     实测通过的版本组合
└── NOTICE.md                 第三方组件与许可
```

## 在 160GB 内存的机器上转换

下面的命令假设都用 `BIMO_ROOT` 指向工作目录，脚本会读这个环境变量。

```bash
export BIMO_ROOT=/data/bimo
mkdir -p $BIMO_ROOT/{models,tok,data,scripts,out,logs}
git clone <this-repo> $BIMO_ROOT/repo && cd $BIMO_ROOT/repo
```

### 1. 建环境

```bash
conda create -p $BIMO_ROOT/envs/bimo python=3.10 -y
source ~/anaconda3/etc/profile.d/conda.sh && conda activate $BIMO_ROOT/envs/bimo

# torch 必须在 auto_gptq 之前装，后者构建期要 import torch
pip install torch==2.6.0 torchvision==0.21.0 -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install -r requirements-lock.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# toolkit 不在 PyPI，装 SDK 里的 wheel
export SDK=/path/to/rknn-llm
pip install --no-deps \
  $SDK/rkllm-toolkit/packages/rkllm_toolkit-1.2.3-cp310-cp310-linux_x86_64.whl

# rkllm 启动要 import pkg_resources
pip install "setuptools<81"
```

有 GPU 的话把 torch 换成对应 CUDA 版本，转换时 `dtype=float16` 能生效，峰值内存直接砍半，速度也快得多。CPU 上 fp16 和 bf16 都会被强制降级成 float32。

### 2. 取权重

```bash
# 四片 safetensors 用 aria2c 多线程，hf 客户端单连接只有 1MB/s 左右
cat > /tmp/aria.txt <<'EOF'
https://hf-mirror.com/iBimo-o/Bimo-7B-CyberAgent/resolve/main/model-00001-of-00004.safetensors
  dir=/data/bimo/models/Bimo-hf
  out=model-00001-of-00004.safetensors
EOF
# 另外三片照此格式追加，然后：
aria2c -i /tmp/aria.txt -x16 -s16 -j4 -k 1M -c --file-allocation=none

# 小文件单独取，加 --include 才不会把分片重下一遍
HF_ENDPOINT=https://hf-mirror.com hf download iBimo-o/Bimo-7B-CyberAgent \
  --include "config.json" "generation_config.json" "tokenizer.json" \
            "tokenizer_config.json" "model.safetensors.index.json" "chat_template.jinja" \
  --local-dir $BIMO_ROOT/models/Bimo-hf
```

下完核对一下哈希，`curl https://hf-mirror.com/api/models/iBimo-o/Bimo-7B-CyberAgent/tree/main` 里的 `lfs.oid` 就是 sha256。

### 3. 修配置

这个仓库是 transformers v5 格式发布的，直接喂 4.x 会报 `'list' object has no attribute 'keys'`，而且 YaRN 参数会被静默丢掉。跑一次补丁脚本：

```bash
python scripts/patch_hf_config.py --model-dir $BIMO_ROOT/models/Bimo-hf
```

脚本可重复执行，第二次跑会提示无需改动，原文件备份成 `*.v5bak`。

### 4. 预检

```bash
python scripts/check_env.py --model-dir $BIMO_ROOT/models/Bimo-hf --sdk $SDK
```

会检查架构、Python 版本、依赖、toolkit 可用性、内存、磁盘、权重完整性，以及配置是否还是 v5 格式。有阻断项就先修，别急着开转。

### 5. 转换

```bash
python scripts/export_bimo.py \
  --source hf --model $BIMO_ROOT/models/Bimo-hf --dtype float32 \
  --qtype w8a8 --algo normal --opt-level 1 \
  --max-context 4096 --cores 3 \
  --data $BIMO_ROOT/data/data_quant_cyber.json \
  --out $BIMO_ROOT/out/Bimo-7B-CyberAgent_w8a8_normal_rk3588_mc4096.rkllm
```

耗时参考：64 核 CPU 上 `--algo none` 是 4 分钟，`--algo normal` 因为要做校准统计会慢不少，按小时计。

转换期间盯着点：

```bash
tr '\r' '\n' < /path/to/log | grep -E '^\[|ERROR' | tail -5
free -g
```

日志里的进度条用 `\r` 刷新，先转成换行才 grep 得到。

### 6. 校验产物

```bash
RK=$BIMO_ROOT/out/*.rkllm
ls -lh $RK          # w8a8 应在 7.6–9.0GiB 之间
md5sum $RK          # 记下来，推板后核对
xxd -l 48 $RK       # 头部 magic 是 daee b336
```

体积低于 7GB 说明 embed 或 lm_head 被量化了，精度风险高；超过 10GB 说明线性层没量化成功。

## 已知的平台限制

这几条是实测出来的，不是文档抄的，动手前值得看一眼。

| 限制 | 说明 |
| --- | --- |
| RK3588 只支持 w8a8 | `w4a16` 及其分组变体全部被拒，toolkit 1.2.1 和 1.2.3 行为一致。SDK 的 `benchmark.md` 里 w4a16 只出现在 RK3576 那一节 |
| GGUF 不能再量化 | `load_gguf` 对 f16 强制 `do_quantization=False`，只能导出 fp16 模型（约 15GB），比 w8a8 还大 |
| CPU 强制 float32 | 日志里明写 `The cpu device not support float16! switch to float32!`，7.6B 权重就要 30.5GB |
| `--algo normal` 要 150GB+ | 优化阶段要单次申请 82,440,945,664 字节（76.8GB）连续内存，且与 `max_context`、`optimization_level`、dtype 都无关，调这些参数不会让需求变小 |
| `max_context` 上限 16384 | 且必须是 32 的倍数。该值写死在模型里，板端实参不能超过它 |

## 板端

模型和 `librkllmrt.so` 推上去之后，用 SDK 自带的 `llm_demo` 就能跑：

```bash
export LD_LIBRARY_PATH=$HOME/bimo/lib:$LD_LIBRARY_PATH
export RKLLM_LOG_LEVEL=1        # 开了才会打印性能与峰值内存
./llm_demo $HOME/bimo/*.rkllm 256 4096
```

完整步骤、四个冒烟用例、常见报错对照表都在 `deploy/smoke_test.md`。

实测数据（LubanCat-5 V2，16GB，内核 6.1.99，rknpu 0.9.8）：初始化 6.7–7.1 秒，prefill 50–56 tok/s，生成 2.95–3.13 tok/s，峰值内存 7.17GB。

## 关于 `--algo none`

如果转换机内存不够，只能退到 `--algo none`。它跳过校准优化，激活量化范围直接按全局极值切分，代价是：

- 回答中途会中英混切
- 生成常在 110–160 token 处提前结束（即使 `max_new_tokens` 设更大）
- 逐行枚举类任务耐心不足，给十行日志只分析前三条就停
- 定向提问的效果明显好于开放式的"全面分析"

如果这些影响业务，还是想办法找台大内存机器重转，`--algo normal` 的差别是实打实的。

## 许可

脚本与文档随本仓库，模型权重遵循 Apache-2.0，RKLLM SDK 相关文件遵循 Rockchip 的条款。详见 `NOTICE.md`。
