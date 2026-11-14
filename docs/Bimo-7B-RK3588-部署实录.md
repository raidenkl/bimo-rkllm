# Bimo-7B-CyberAgent 移植到 RK3588 的实践记录

## 一、背景与目标

Bimo-7B-CyberAgent 是一个面向防御性安全的 7B 模型，底座为 Qwen2.5-Coder-7B-Instruct，参数量 7,615,616,512，BF16 权重约 15.2GB，词表 152,064（比常见的 151,936 多出占位 token），训练时用 YaRN 把有效上下文从 8K 扩到 32K。

目标平台是 Rockchip RK3588，具体机型为 Embedfire LubanCat-5 V2，16GB 内存。要把这个模型以 RKLLM 格式跑在板卡的 NPU 上，并验证推理可用。

初始设想的技术路线是取 mradermacher 仓库里的 GGUF 量化版直接转 RKLLM，因为 GGUF 是单文件、体积小、还有现成的镜像加速。事后看，这条路走不通，但绕路的过程留下了一些值得记下来的东西。

最终交付的是：

- 模型文件 `Bimo-7B-CyberAgent_w8a8_rk3588_mc4096.rkllm`，8,210,157,916 字节（7.65GiB）
- 与模型同版本的 aarch64 运行时 `librkllmrt.so`（1.2.3）
- 板端编译脚本与四条冒烟用例的实测结论

---

## 二、动手前的环境盘查

### 2.1 两台候选转换机

最初指定的转换机是 10.0.0.141，检测之后发现它扛不住 7B 模型：

| 项目       | 10.0.0.141                | 10.0.0.150                |
| -------- | ----------------------------- | ----------------------------- |
| CPU / 内存 | 4 核 / 16GB（可用约 13GB）          | 64 核 / 62GB（可用约 55GB）         |
| GPU      | 无（VMware 虚拟机）                 | 无                             |
| 磁盘可用     | 116GB                         | 471GB                         |
| 已有工具链    | rkllm-toolkit 1.2.1、SDK 1.2.1 | rkllm-toolkit 1.2.3、SDK 1.2.3 |
| 结论       | 加载阶段就会 OOM                    | 实际采用                          |

判断依据很直接：fp32 权重就要 30.5GB，.141 的 13GB 连门都进不去。这个结论跟 CPU 核数无关，纯粹是内存下限问题。

### 2.2 板卡条件

LubanCat-5 V2，Debian 12，内核 6.1.99-rk3588，rknpu 驱动 0.9.8，15,945MB 内存，根分区 105GB 可用。板上自带 gcc 12.2 和 cmake 3.25，glibc 2.36，可以原生编译，省掉交叉工具链的问题。

板卡没装 rsync，大批量传输要用 scp 加密钥认证。

---

## 三、实施过程

下面每个阶段末尾都附了当时实际执行的命令。几点约定：工作目录统一是 `/home/dev/ljh/bimo`，`python` 指代已激活的 `bimo_rkllm` 环境（非交互场景建议直接写 `.../envs/bimo_rkllm/bin/python` 的绝对路径，机器上 `auto_activate: false`，conda 不在 PATH 里）；`scripts/` 下的四个脚本是本次自己写的，`export_bimo.py` 做转换、`build_calib.py` 生成校准集、`dump_gguf_meta.py` 解析 GGUF 头、`make_long_seeds.py` 扩充长样本。

### 3.1 阶段一：搭一套隔离的转换环境

在 .150 上用 conda 建了独立前缀环境，避免污染机器上已有的 `rkllm1.2.1`：

```bash
# 变量：SDK 里带 wheel 和依赖清单
SDK=/home/dev/ljh/qwen2/rknn-llm

# 1) 建环境（python 3.10.20 已在 conda 包缓存里，离线也能建）
~/anaconda3/bin/conda create -p /home/dev/ljh/bimo/envs/bimo_rkllm python=3.10 -y

# 2) 先装 torch，再装其余依赖。auto_gptq 构建期要 import torch，顺序反了会失败
pip install torch==2.6.0 torchvision==0.21.0 -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3) 装 SDK 指定的依赖清单（精确 pin，含 transformers 4.55.2 / numpy 1.26.4）
pip install -r $SDK/rkllm-toolkit/packages/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 4) 装 toolkit 本体。--no-deps 避免二次解析把版本搅乱
pip install --no-deps \
  $SDK/rkllm-toolkit/packages/rkllm_toolkit-1.2.3-cp310-cp310-linux_x86_64.whl

# 5) 补 setuptools，否则 import rkllm 时报 No module named 'pkg_resources'
pip install "setuptools<81" -i https://pypi.tuna.tsinghua.edu.cn/simple

# 6) 校验：版本组合与 build 签名
python -c "import sys,torch,transformers,numpy; \
print(sys.version.split()[0], torch.__version__, transformers.__version__, numpy.__version__)"
python -c "from rkllm.api import RKLLM; import inspect; print(inspect.signature(RKLLM.build))"
```

依赖清单里有 `auto_gptq==0.7.1`，这类包容易在构建阶段卡住，所以先装 torch 再装其余。`rkllm-toolkit` 不在 PyPI 上（`pip index versions` 查不到），只能装 SDK 里带的本地 wheel。

装完发现还缺 `setuptools`，`rkllm` 启动时要 import `pkg_resources`，补装 `setuptools<81` 才通。

`auto_gptq` 因为 peft 版本过新，import 会失败（`cannot import name 'PEFT_TYPE_TO_MODEL_MAPPING'`）。查过 rkllm 的 Python 层并不引用它，w8a8 路径用不到，就没管。

### 3.2 阶段二：取权重并校验

先下了 GGUF 的 f16 版本，14.19GiB 单文件，aria2c 十六线程五分多钟就跑完了。后来改走 safetensors，四个分片共 15,231,275,840 字节。

下载的时候用多线程下载会很快。

```bash
# 1) 取 GGUF f16：单文件、16 连接、支持断点续传
aria2c -x16 -s16 -k 1M -c --file-allocation=none \
  -d /home/dev/ljh/bimo/models -o Bimo-7B-CyberAgent.f16.gguf \
  "https://hf-mirror.com/mradermacher/Bimo-7B-CyberAgent-GGUF/resolve/main/Bimo-7B-CyberAgent.f16.gguf"

# 2) 取 safetensors 四片：-j4 让四个文件并行，每个文件内部仍开 16 连接
cat > /tmp/bimo_aria.txt <<'EOF'
https://hf-mirror.com/iBimo-o/Bimo-7B-CyberAgent/resolve/main/model-00001-of-00004.safetensors
  dir=/home/dev/ljh/bimo/models/Bimo-hf
  out=model-00001-of-00004.safetensors
https://hf-mirror.com/iBimo-o/Bimo-7B-CyberAgent/resolve/main/model-00002-of-00004.safetensors
  dir=/home/dev/ljh/bimo/models/Bimo-hf
  out=model-00002-of-00004.safetensors
https://hf-mirror.com/iBimo-o/Bimo-7B-CyberAgent/resolve/main/model-00003-of-00004.safetensors
  dir=/home/dev/ljh/bimo/models/Bimo-hf
  out=model-00003-of-00004.safetensors
https://hf-mirror.com/iBimo-o/Bimo-7B-CyberAgent/resolve/main/model-00004-of-00004.safetensors
  dir=/home/dev/ljh/bimo/models/Bimo-hf
  out=model-00004-of-00004.safetensors
EOF
aria2c -i /tmp/bimo_aria.txt -x16 -s16 -j4 -k 1M -c --file-allocation=none

# 3) 小文件用 hf 客户端补齐。务必加 --include，否则会把四个分片重新拉一遍
HF_ENDPOINT=https://hf-mirror.com hf download iBimo-o/Bimo-7B-CyberAgent \
  --include "config.json" "generation_config.json" "tokenizer.json" \
            "tokenizer_config.json" "model.safetensors.index.json" "chat_template.jinja" \
  --local-dir /home/dev/ljh/bimo/models/Bimo-hf

# 4) 校验一：拉仓库清单，比对每个分片的字节数与 sha256
curl -sSL "https://hf-mirror.com/api/models/iBimo-o/Bimo-7B-CyberAgent/tree/main" \
  | python3 -c "import sys,json
for f in json.load(sys.stdin):
    if f['path'].endswith('.safetensors'):
        print(f['path'], f['size'], f.get('lfs',{}).get('oid'))"

# 5) 校验二：抽一个分片算哈希，与上一步的 oid 对
sha256sum /home/dev/ljh/bimo/models/Bimo-hf/model-00004-of-00004.safetensors

# 6) 校验三：确认四个分片齐了、体积对得上
ls -l /home/dev/ljh/bimo/models/Bimo-hf/
```

三个校验步骤是递进的：先确认仓库那边给出的目标值，再验证本地文件内容没坏，最后核对分片数量。

### 3.3 阶段三：GGUF 路线行不通

按原计划调用 `load_gguf`，加载倒是成功了，但 build 直接报错：

```
ERROR: do_quantization must be False, when GGMLQuantizationType is MOSTLY_F16!
```

意思是 f16 的 GGUF 在 RKLLM 眼里已经算"量化过"的格式，只允许原样导出，不允许再量化成 w8a8。这样一来 GGUF 路线只能产出 15GB 左右的 fp16 模型，比 w8a8 还大，对 16GB 板毫无意义。

顺带说一句，仓库里也没有 Q4_0 档位，而 `load_gguf` 恰好只认 Q4_0 和 f16 两种。所以 GGUF 这条线彻底断了，只能回到 HF safetensors。

```bash
# 1) 先花两分钟 dump 头部元数据，确认这个文件到底能不能被 load_gguf 接受
python scripts/dump_gguf_meta.py models/Bimo-7B-CyberAgent.f16.gguf | tee logs/gguf_meta.txt
#    断言三项：general.file_type=1（MOSTLY_F16）、tokenizer.chat_template 非空、
#    qwen2.rope.scaling.type=yarn。三项全过才开始正式转换

# 2) 按原计划试图从 GGUF 直接量化，验证这条路是否可行
python - <<'PY'
from rkllm.api import RKLLM
llm = RKLLM()
print(llm.load_gguf("models/Bimo-7B-CyberAgent.f16.gguf"))          # 返回 0，加载成功
print(llm.build(do_quantization=True, quantized_dtype="w8a8",        # 返回 -1，被拒
                quantized_algorithm="normal", target_platform="rk3588",
                num_npu_core=3, dataset="data/data_quant_cyber.json",
                hybrid_rate=0, max_context=4096))
PY
```

第二步返回 -1 的同时打印出上面那条 `do_quantization must be False`，到这里 GGUF 路线就死掉了。后面的 `export_bimo.py` 里我加了一道硬拦截，`--source gguf` 配量化会直接退出，不再浪费一次加载时间。

### 3.4 阶段四：safetensors 路线的两个坑

换到 `load_huggingface` 之后，先是报了一个很莫名的错误：

```
ERROR: Catch exception when loading model: 'list' object has no attribute 'keys'
```

翻配置文件才明白，这个模型仓库是按 transformers v5 的格式发布的（`transformers_version: 5.22.0`），而环境里是 4.55.2。`tokenizer_config.json` 里的 `extra_special_tokens` 在 v5 是列表，v4 期望的是字典，于是 `.keys()` 就炸了。

同一批 v5 字段还带来第二个隐患：模型用 `rope_parameters` 描述 YaRN，v4 只认 `rope_scaling`。如果不管，YaRN 参数会被静默丢弃，8K 以上的长上下文直接失效。

处理办法是写个小脚本把配置倒回 v4 格式：删掉 `extra_special_tokens` 和 `layer_types`，把 `rope_parameters` 翻译成 `rope_scaling: {rope_type: yarn, factor: 4.0, original_max_position_embeddings: 8192}`，再补上 `rope_theta: 10000`。

改完 `load_weight=False` 试了一把，返回 0，配置解析这关过了。

```bash
# 1) 先备份，再改两份配置（config.json 管模型结构，tokenizer_config.json 管分词器）
D=/home/dev/ljh/bimo/models/Bimo-hf
cp -n $D/config.json $D/config.json.v5bak
cp -n $D/tokenizer_config.json $D/tokenizer_config.json.v5bak

python3 - "$D" <<'PY'
import json, sys, os
D = sys.argv[1]

# config.json：rope_parameters -> rope_scaling，去掉 v5 专有字段
p = os.path.join(D, "config.json")
c = json.load(open(p))
rp = c.pop("rope_parameters", None)
if rp:
    c["rope_scaling"] = {
        "rope_type": rp.get("rope_type", rp.get("type", "yarn")),
        "factor": rp.get("factor", 4.0),
        "original_max_position_embeddings": rp.get("original_max_position_embeddings", 8192),
    }
    c["rope_theta"] = rp.get("rope_theta", 10000.0)
c.pop("layer_types", None)          # v5 专有，v4 不认
c["torch_dtype"] = c.get("dtype", "bfloat16")
json.dump(c, open(p, "w"), indent=2, ensure_ascii=False)

# tokenizer_config.json：extra_special_tokens 是 list，v4 会当 dict 用
p2 = os.path.join(D, "tokenizer_config.json")
t = json.load(open(p2))
t.pop("extra_special_tokens", None)
json.dump(t, open(p2, "w"), indent=2, ensure_ascii=False)
print("patched")
PY

# 2) 不加载权重，只验证配置与分词器能否解析（几秒钟，不占 30GB）
python - <<'PY'
from rkllm.api import RKLLM
print(RKLLM().load_huggingface(model="/home/dev/ljh/bimo/models/Bimo-hf",
                               device="cpu", dtype="float32", load_weight=False))
PY
```

`load_weight=False` 这个开关很实用：它把模型结构和 tokenizer 都走一遍，但跳过 15GB 权重，配置类错误几秒内就能暴露，不用每次都等半分钟加载。

### 3.5 阶段五：量化参数连试四轮

真正的麻烦在 build 阶段。头一次用 `w4a16_g128`，被拒：

```
ERROR: target_platform: rk3588 not support quantized_dtype: w4a16_g128!
```

换成不分组 `w4a16` 还是一样。为了确认不是版本问题，专门跑到 .141 上用 toolkit 1.2.1 复测，同样拒绝。翻 SDK 的 `benchmark.md` 才看明白：RK3588 那一节的量化类型清一色是 w8a8，w4a16 出现在 RK3576 的表格里。这是芯片能力边界，不是配置问题。

那就用 w8a8。结果第二次撞墙，内存不足：

```
DefaultCPUAllocator: can't allocate memory: you tried to allocate 82440945664 bytes
```

82,440,945,664 字节，76.8GB，而且是单次连续分配。当时进程已经占了 59GB，加起来要 136GB，机器总共才 62GB。

试过几轮排查看是不是参数能压下来：`optimization_level` 从 1 降到 0，`max_context` 从 4096 降到 2048，dtype 换成 bfloat16，申请量一个字节都没变。`normal_r1` 也一样。dtype 那条尤其无解，CPU 上 fp16 和 bf16 都会被强制转成 float32（日志里明写 `The cpu device not support float16! switch to float32!`），权重翻倍这件事绕不过去。

最后试 `--algo none`，跳过校准优化阶段，build 89 秒通过，峰值 33GB。分析阶段需要的 76.8GB 缓冲正是被这一步省掉的。

代价是这个算法不做校准统计，激活量化范围直接按 min-max 出，精度会比 `normal` 差一些。当时没有别的选择。

```bash
# === 第 0 步：准备校准集（build 阶段要用，先备着）===
# 只下 tokenizer，几 MB，不用把 15GB 权重再拉一遍
hf download Qwen/Qwen2.5-Coder-7B-Instruct \
  --include "tokenizer.json" "tokenizer_config.json" "vocab.json" "merges.txt" \
  --local-dir /home/dev/ljh/bimo/tok --endpoint https://hf-mirror.com

# seeds.jsonl 是 187 条安全域样本，脚本负责渲染 ChatML 模板、按长度过滤
python scripts/build_calib.py --min-tok 30 --max-tok 1600
#  → kept=187  p50=94  p90=389  max=1573  合计 32,316 token

# === 第 1、2 轮：试 4bit，两次都被平台拒绝 ===
python scripts/export_bimo.py --source hf --model models/Bimo-hf --dtype float32 \
  --qtype w4a16_g128 --algo normal --max-context 4096 --cores 3 --out out/try1.rkllm
#  → target_platform: rk3588 not support quantized_dtype: w4a16_g128!

python scripts/export_bimo.py --source hf --model models/Bimo-hf --dtype float32 \
  --qtype w4a16 --algo normal --max-context 4096 --cores 3 --out out/try2.rkllm
#  → not support quantized_dtype: w4a16

# === 第 3 轮：换到 .141 用 toolkit 1.2.1 复测，排除版本因素 ===
# 在 10.0.0.141 上，用 0.6B 小模型 + load_weight=False，绕开内存限制
cat > /tmp/probe_dtype.py <<'PY'
from rkllm.api import RKLLM
import sys
llm = RKLLM()
llm.load_huggingface(model="/home/dev/ai/Qwen3-0.6B", device="cpu",
                     dtype="float32", load_weight=False)
print("ret =", llm.build(do_quantization=True, quantized_dtype=sys.argv[1],
                         quantized_algorithm="normal", target_platform="rk3588",
                         dataset=None, hybrid_rate=0, max_context=32))
PY
python /tmp/probe_dtype.py w4a16
#  → 1.2.1 同样拒绝，确认是芯片能力边界而非版本问题

# === 第 4 轮：w8a8 + normal，撞内存墙 ===
python scripts/export_bimo.py --source hf --model models/Bimo-hf --dtype float32 \
  --qtype w8a8 --algo normal --opt-level 1 --max-context 4096 --cores 3 \
  --out out/w8a8_normal.rkllm
#  → can't allocate memory: you tried to allocate 82440945664 bytes

# === 第 5-8 轮：逐个参数往下压，申请量纹丝不动 ===
python scripts/export_bimo.py ... --qtype w8a8 --algo normal --opt-level 0 ...
python scripts/export_bimo.py ... --qtype w8a8 --algo normal --max-context 2048 ...
python scripts/export_bimo.py ... --qtype w8a8 --algo normal --dtype bfloat16 ...
python scripts/export_bimo.py ... --qtype w8a8 --algo normal_r1 ...
#  → 四次都是同一个 82440945664

# === 第 9 轮：跳过校准优化，通过 ===
python scripts/export_bimo.py --source hf --model models/Bimo-hf --dtype float32 \
  --qtype w8a8 --algo none --opt-level 0 --max-context 4096 --cores 3 \
  --out out/Bimo-7B-CyberAgent_w8a8_rk3588_mc4096.rkllm
#  → load 24s / build 89s / export 约 2min，峰值 37.69GB，产物 7.65GiB

# 转换期间用这两条盯着，日志里的 \r 进度条要转到换行才 grep 得到
tr '\r' '\n' < logs/export_w8a8.log | grep -E '^\[|ERROR' | tail -5
free -g
```

### 3.6 阶段六：板端部署与验证

传输走了密钥认证（板卡无 rsync），scp 实测 114MB/s，7.65GB 一分多钟传完。板上编译 `llm_demo` 只花几秒，因为 gcc 12.2 是原生的。

第一次启动就成功了，日志里版本对得很齐：

```
rkllm-runtime version: 1.2.3, rknpu driver version: 0.9.8, platform: RK3588
rkllm-toolkit version: 1.2.3, max_context_limit: 4096, npu_core_num: 3,
target_platform: RK3588, model_dtype: W8A8
rkllm init success
```

之前一直担心 chat_template 没写进模型，实测下来是白担心：问"你是谁"，它答"我是Bimo，一个自主的网络安全审计和系统情报专家"，说明那 560 字节的模板确实随模型一起打包了，`rkllm_set_chat_template` 这个兜底没用上。

传输和编译这一段的命令，按执行顺序分两块。

第一块在转换机上跑：

```bash
# 1) 先探板端条件：架构、内存、gcc、glibc 版本决定能不能原生编译
ssh cat@192.168.1.140 'uname -m; free -m; gcc --version | head -1; ldd --version | head -1'

# 2) 配免密：板卡没装 rsync，7.65GB 用 scp 传，每次都输密码不现实
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -q          # 已存在则跳过
PUB=$(cat ~/.ssh/id_ed25519.pub)
ssh cat@192.168.1.140 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && \
  grep -qF '$PUB' ~/.ssh/authorized_keys || echo '$PUB' >> ~/.ssh/authorized_keys"
ssh -o BatchMode=yes cat@192.168.1.140 'echo KEY_OK'        # 验证免密生效

# 3) 小文件先打包传（运行时库 + demo 源码 + 文档，2.3MB）
cd /home/dev/ljh/bimo/deploy
tar czf /tmp/bimo_payload.tar.gz librkllmrt.so rkllm_api_demo smoke_test.md
ssh cat@192.168.1.140 'mkdir -p ~/bimo/lib'
scp /tmp/bimo_payload.tar.gz cat@192.168.1.140:~/bimo/

# 4) 模型单独传，8.21GB，实测 114MB/s
scp out/Bimo-7B-CyberAgent_w8a8_rk3588_mc4096.rkllm cat@192.168.1.140:~/bimo/

# 5) 核对字节数，确认传输没截断
ssh cat@192.168.1.140 'stat -c %s ~/bimo/*.rkllm'           # 应为 8210157916
```

第二块在板卡上跑：

```bash
# 1) 解包并摆好运行时库位置（demo 默认去 ../lib 找）
cd ~/bimo && tar xzf bimo_payload.tar.gz && cp librkllmrt.so lib/

# 2) 原生编译，脚本会自动检查 runtime 版本并静态链接 libstdc++
cd ~/bimo/rkllm_api_demo && bash build_on_board.sh
#    产出 llm_demo（1.8MB aarch64 ELF）

# 3) 冒烟：身份验证 + 生成性能
export LD_LIBRARY_PATH=$HOME/bimo/lib:$LD_LIBRARY_PATH
export RKLLM_LOG_LEVEL=1                     # 只有开了才会打印 prefill/generate/峰值内存
printf '你是谁？用一句话介绍自己。\nexit\n' | \
  ./llm_demo $HOME/bimo/Bimo-7B-CyberAgent_w8a8_rk3588_mc4096.rkllm 96 4096

# 4) 多轮用例：把问题写进文件后重定向，避免长文本里的引号在 shell 层被吃掉
./llm_demo $HOME/bimo/*.rkllm 200 4096 < /tmp/bimo_in.txt

# 5) 完整性复核（可选，8GB 算一遍约一分钟）
md5sum ~/bimo/*.rkllm                        # 应为 b29764c3e142c6fe89182a71787d921f
```

第三条命令里的 `96` 是 max_new_tokens，`4096` 是 max_context，后者不能超过转换时写死的值，否则 init 阶段直接失败。

---

## 四、关键决策与注意事项

### 4.1 四条平台限制，动手前就该知道

| 限制                | 实测结论                                               |
| ----------------- | -------------------------------------------------- |
| RK3588 支持的量化类型    | 只有 w8a8。w4a16 系列全部被拒，1.2.1 和 1.2.3 行为一致            |
| GGUF 能否再量化        | 不能。f16 GGUF 必须 `do_quantization=False`，只能出 fp16 模型 |
| CPU 上的 dtype      | fp16 与 bf16 会被强制降为 float32，权重内存翻倍                  |
| `normal` 类算法的内存门槛 | 需要 76.8GB 单次连续分配，7B 模型建议准备 ≥160GB 内存的机器            |

### 4.2 容易踩的几个坑

|   |   |   |   |   |   |
| - | - | - | - | - | - |

- `pipefail` 配 `strings | grep -m1` 会因 SIGPIPE 返回非零，导致 `|| echo unknown` 误触发。脚本里要么 `|| true` 兜住，要么临时关掉 pipefail。
- 板端 `free -m` 输出的是中文"内存："，按 `Mem:` 匹配的 awk 一律失效。改用 `/proc/meminfo` 更稳。
- 远程执行 `pkill -f 'xxx'` 时，如果脚本正文里也含这个字符串，会把执行脚本的 shell 自己杀掉。加个方括号 `pkill -f '[x]xx'` 就避开了。
- `llm_demo` 是逐行读 stdin 的，多行日志会被当成多条独立提问。做长上下文测试时要把日志合成单行，用分隔符串起来。
- 板端 runtime 必须和转换工具链同版本。版本不匹配的表现是 `rkllm_init` 返回非 0，日志里出现 `version check encountered an error`，容易被误判成模型损坏。

### 4.3 参数怎么定的

| 参数                    | 取值   | 理由                            |
| --------------------- | ---- | ----------------------------- |
| `quantized_dtype`     | w8a8 | RK3588 唯一选项                   |
| `quantized_algorithm` | none | normal 及其变体都要 76.8GB，本机跑不了    |
| `optimization_level`  | 0    | 与 `algo=none` 搭配，速度内存优先       |
| `num_npu_core`        | 3    | 三核吞吐最好，且该值写死在模型里，板端改不了        |
| `max_context`         | 4096 | 模型原生窗口 8192 的一半，KV 只占 224MB，稳 |

校准集准备了 187 条安全域样本（工具调用、CVE 研判、代码审计、日志分析、红队流程、通用问答六个子域），共 32,316 token。因为最后用了 `algo=none`，这批数据本次没被消费，留在服务器上等下次重转时用。

---

## 五、结论与后续建议

### 5.1 实测数据

| 指标           | 数值                               |
| ------------ | -------------------------------- |
| 板端模型初始化      | 6.7 至 7.1 秒                      |
| Prefill      | 49.7 至 55.9 tok/s                |
| Generate     | 2.95 至 3.13 tok/s（约 320ms/token） |
| 峰值内存         | 7.17GB                           |
| 914 token 预填 | 首字延迟 17.3 秒                      |

16GB 板加载后还剩 7.7GB 左右，同时跑别的服务问题不大。这个结果也回过头印证了前面的判断：如果板子真是 8GB，w8a8 的 7.17GB 加上系统和 KV，绝对装不下。

### 5.2 质量表现

四条用例的结果不太均匀，如实记录：

- 身份识别通过，模板生效。
- JSON 工具调用基本可用。输出是 `{"name":"get_cve_info","arguments":{"cve_id":"CVE-2021-44228","include_references":true}}`，结构合法，布尔值也没退化成字符串。但键名用了 `name` 而不是常见的 `tool`（没给 schema 的情况下算合理选择），外面还包了一层 json 代码围栏。
- 代码审计通过。SQL 注入判断准确，参数化查询的改法也对，只是示例代码里多写了一个括号。
- 长上下文只算部分通过。给十行日志让它全面分析，它列到第三条就停了，没发现日志里 `/upload` 配合 `/uploads/shell.php?cmd=id` 这条已经成功的 Web Shell 链条。换成定向提问（只问"是否已植入 Web Shell"）能答对，但举证时把正常的 `/static/app.js` 请求当成了文件泄露尝试。

还有几个反复出现的现象：回答到一半从中文切英文，生成常在 110 到 160 token 处提前结束（即使 `max_new_tokens` 设到 320 也一样），逐行枚举类的任务明显缺乏耐心。

这些都能归到同一个原因上：`algo=none` 不做校准，w8a8 的激活量化范围是按全局极值切出来的，对结构化输出和长文本推理的损伤比正常校准更明显。

### 5.3 建议

按优先级排：

1. 如果要用于生产，优先解决量化质量问题。办法是找一台 ≥160GB 内存的机器（有 GPU 更好，fp16 能少占一半），用 `--algo normal --opt-level 1` 重转一次。命令已经写好在 `export_bimo.py` 里，校准集也备好了，换机器直接跑即可。
2. 短期使用的话，把交互方式从"全面分析"改成"定向提问"。实测差别很明显，同一个模型、同一段日志，换个问法结论就对上了。
3. 如果业务对延迟敏感，可以再出一个 `optimization_level=0` 加更小 `max_context`（比如 2048）的版本做对比。KV 减半能省 112MB，但对 3 tok/s 的生成速度帮助有限，瓶颈在权重读取带宽，不在 KV 缓存。
4. 上板前建议先跑一遍 `build_on_board.sh` 里的环境检查段，尤其是 runtime 版本和 `ldd` 结果。这次一次成功运气不错，版本错配是老平台最常见的翻车点。

---

## 附：文件位置

转换机 `10.0.0.150`：

```
/home/dev/ljh/bimo/
├── envs/bimo_rkllm/                    隔离环境（tk 1.2.3 + torch 2.6.0）
├── models/Bimo-hf/                     15.23GB safetensors（已转 v4 配置格式）
├── models/Bimo-7B-CyberAgent.f16.gguf  GGUF 备份，14.19GiB
├── data/data_quant_cyber.json          187 条校准集，本次未使用
├── scripts/                            export_bimo.py / build_calib.py / dump_gguf_meta.py
└── out/Bimo-7B-CyberAgent_w8a8_rk3588_mc4096.rkllm
```

板卡 `192.168.1.140`：

```
/home/cat/bimo/
├── Bimo-7B-CyberAgent_w8a8_rk3588_mc4096.rkllm
├── lib/librkllmrt.so
└── rkllm_api_demo/{llm_demo, llm_demo.cpp, build_on_board.sh, include/}
```
