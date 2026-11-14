# Bimo-7B-CyberAgent 在 RK3588(16GB) 上的部署与验证

产物：`Bimo-7B-CyberAgent_w8a8_rk3588_mc4096.rkllm`（8,210,157,916 字节 / 7.65 GiB）
量化：w8a8，`max_context=4096` 已写入模型，`num_npu_core=3` 已写入模型

---

## 0. 前置条件（务必先核对）

| 项 | 要求 | 检查命令 |
|---|---|---|
| 内存 | ≥16GB（模型 7.65GB + KV + runtime，实测预期占用 7.5–8.5GB） | `free -m` |
| NPU 驱动 | rknpu 0.9.6 以上（SDK 自带 0.9.8） | `cat /sys/kernel/debug/rknpu/version` |
| glibc / libstdc++ | `GLIBC ≤ 2.29`、`GLIBCXX ≤ 3.4.26` | `ldd --version`、`strings /usr/lib*/libstdc++.so.6 \| grep GLIBCXX \| tail -1` |
| 运行时版本 | **必须用交付包里的 `librkllmrt.so`（1.2.3）**，禁用板厂自带旧版 | `strings lib/librkllmrt.so \| grep "RKLLM SDK"` |
| 磁盘 | `/data` 剩余 ≥10GB | `df -h /data` |

> 版本不匹配是最常见的失败原因。模型由 rkllm-toolkit **1.2.3** 生成，板端 runtime 低于此版本会在 `rkllm_init` 返回非 0，日志出现 `version check encountered an error` 或 `is lower than the minimum required version`。

---

## 1. 传输

在转换机（10.0.0.150）上：

```bash
cd /home/dev/ljh/bimo/deploy
BOARD_HOST=root@<板卡IP> BOARD_DIR=/data/bimo bash push_to_board.sh
```

或走 adb：

```bash
BOARD_HOST=x bash push_to_board.sh --adb
```

传完后板上有：

```
/data/bimo/
├── Bimo-7B-CyberAgent_w8a8_rk3588_mc4096.rkllm   7.65GiB
├── lib/librkllmrt.so
└── rkllm_api_demo/{llm_demo.cpp, build_on_board.sh, include/}
```

---

## 2. 编译

板上执行：

```bash
cd /data/bimo/rkllm_api_demo && bash build_on_board.sh
```

脚本用 `-static-libstdc++ -static-libgcc` 静态链接 C++ 运行库，避免板端 `GLIBCXX_3.4.26 not found`。若板上无 g++，见文末「交叉编译」。

---

## 3. 运行

```bash
export LD_LIBRARY_PATH=/data/bimo/lib:$LD_LIBRARY_PATH
export RKLLM_LOG_LEVEL=1                     # 打印性能与内存，便于评估
cd /data/bimo/rkllm_api_demo
./llm_demo /data/bimo/Bimo-7B-CyberAgent_w8a8_rk3588_mc4096.rkllm 256 4096
```

参数：`llm_demo <model> <max_new_tokens> <max_context_len>`

- 第 3 个参数**不得大于**转换时的 4096
- 内存吃紧时可降到 `2048`：KV 从 224MB 降到 112MB（每 token 约 56KB）
- demo 源码里 `param.extend_param.embed_flash = 1` 默认已开，embedding 从 flash 流式读取，比常驻省约 1GB

同时另开一个终端观察内存峰值：

```bash
while true; do free -m | awk '/Mem:/{printf "%s used=%sMB avail=%sMB\n", strftime("%H:%M:%S"), $3, $7}'; sleep 2; done
```

---

## 4. 冒烟用例（4 条，覆盖 4 类风险）

| # | 输入 | 期望 | 验证目标 |
|---|---|---|---|
| 1 | `你是谁？用一句话介绍自己。` | 回答中出现 **Bimo**（而非泛泛的"我是一个AI助手"） | chat_template 的默认 system prompt 是否随模型生效 |
| 2 | `只输出 JSON，不要解释。调用工具 get_cve_info，参数 cve_id="CVE-2021-44228"。` | 输出形如 `{"tool":"get_cve_info","arguments":{"cve_id":"CVE-2021-44228"}}` | function calling 是否退化（w8a8 最易损伤的能力） |
| 3 | `审计这段代码的注入风险：cur.execute("SELECT * FROM u WHERE n='%s'" % name)` | 指出 SQL 注入、给出参数化查询修法 | 安全域知识保留度 |
| 4 | 贴入约 30 行 nginx 访问日志，问「归纳攻击链」 | 能指出扫描/注入阶段并给出证据行 | 长上下文与日志理解 |

记录每条的实际输出。**用例 2 是重点**：若 JSON 字段缺失或格式错乱，说明量化损伤了结构化输出能力。

---

## 5. 若用例 1 失败（模板未生效）

现象：`llm_demo` 启动即报 `Chat template auto-parsing failed`，或回答不认自己是 Bimo。

原因：模板需从 `chat_template.jinja` 随模型写入 `.rkllm`。本次转换时该文件已存在（560 字节，与 GGUF 内嵌模板逐字符一致），但产物为完整二进制打包，无法在主机侧预先确认是否写入。

兜底方案（改 demo 源码，在 `rkllm_init` 成功后加一行）：

```cpp
const char* BIMO_SYS =
    "You are Bimo, an autonomous cybersecurity and systems intelligence agent created by "
    "Saim Azam. You specialize in ethical security auditing, vulnerability research, native "
    "JSON function calling, and multi-turn tool execution. When asked about your identity, "
    "always introduce yourself as Bimo.";

rkllm_set_chat_template(handle, BIMO_SYS,
                        "<|im_start|>user\n",
                        "<|im_end|>\n<|im_start|>assistant\n");
```

重新 `bash build_on_board.sh` 即可。

---

## 6. 常见错误对照

| 报错 | 原因 | 处置 |
|---|---|---|
| `rkllm_init` 返回非 0 + `version check encountered an error` | runtime 版本低于 1.2.3 | 用交付包里的 `librkllmrt.so`，`LD_LIBRARY_PATH` 放在最前 |
| `error while loading shared libraries: libGLIBCXX_3.4.26` | 板端 libstdc++ 过旧 | 用本包 `build_on_board.sh`（静态链接）重编；或升级固件 |
| `rkllm_init` 返回 -1 且 dmesg 有 OOM | 内存不足 | `max_context` 降到 2048；关闭桌面/其他服务；确认 `embed_flash=1` |
| 输出乱码或重复 | tokenizer 不匹配 | 确认用的是本次交付的 `.rkllm`，不要混用其他版本 |
| 首次 init 很久（10–25s） | 7.65GB 从 eMMC 读入，正常 | 可放到 eMMC 的连续区域或 NVMe 以加速 |

---

## 7. 预期性能（16GB / 三核 NPU）

| 指标 | 预期 |
|---|---|
| 生成速度 | 3.5–5 tok/s |
| TTFT（128 ctx / 64 new） | 1.7–2.2 s |
| 常驻内存 | 7.5–8.5 GB |
| 首次 init | 10–25 s |

（依据 SDK `benchmark.md` 中 ChatGLM3-6B w8a8 = 4.94 tok/s、内存 5976MB，按 7.6B 线性外推。本模型词表 152k，embed 侧比 ChatGLM3 更占内存，故内存上探。）

---

## 附录：交叉编译（板上无 g++ 时）

在转换机（x86）上：

```bash
cd /home/dev/ljh/bimo/deploy/rkllm_api_demo
aarch64-linux-gnu-g++ -O2 -std=c++17 llm_demo.cpp \
  -I./include -L../lib -lrkllmrt \
  -Wl,-rpath,'$ORIGIN/../lib' \
  -static-libstdc++ -static-libgcc \
  -o llm_demo_aarch64
```

然后只把 `llm_demo_aarch64` 推到板上。注意 `-static-libstdc++` 不可省：主机 gcc 11 会引入 `GLIBCXX_3.4.29` 依赖，老固件加载不了。

---

## 8. 实测结果（2026-11-14，LubanCat-5 V2 / RK3588 / 16GB）

**环境**：Debian 12，内核 6.1.99-rk3588，rknpu driver 0.9.8，gcc 12.2，glibc 2.36

**加载**：

```
rkllm-runtime version: 1.2.3, rknpu driver version: 0.9.8, platform: RK3588
rkllm-toolkit version: 1.2.3, max_context_limit: 4096, npu_core_num: 3,
target_platform: RK3588, model_dtype: W8A8
Enabled cpus: [4, 5, 6, 7]
rkllm init success
```

版本完全匹配，一次加载成功，**无需**调用 `rkllm_set_chat_template`（模型内嵌模板生效）。

**性能**：

| 指标 | 实测 |
|---|---|
| Model init | 6.7 – 7.1 s |
| Prefill | 49.7 – 55.9 tok/s |
| Generate | **2.95 – 3.13 tok/s** |
| Peak Memory | **7.17 GB** |
| 长上下文（914 tok 预填） | TTFT 17.3 s，generate 2.95 tok/s |

16GB 板余量约 7.7GB，可同时跑其他服务。

**4 条用例结果**：

| # | 结果 | 说明 |
|---|---|---|
| 1 身份 | ✅ 通过 | 回答「我是Bimo，一个自主的网络安全审计和系统情报专家。」——**chat_template 随模型生效** |
| 2 JSON 工具调用 | ⚠️ 基本通过 | 输出 `{"name": "get_cve_info", "arguments": {"cve_id": "CVE-2021-44228", "include_references": true}}`；结构合法、`true` 为布尔（非字符串）。但键名用 `name` 而非 `tool`（未给 schema 时属合理选择），且外包了 ` ```json ` 围栏 |
| 3 代码审计 | ✅ 通过 | 准确判定 SQL 注入并给出参数化查询修法；代码示例有 `(name,)))` 多余括号的小笔误 |
| 4 长上下文 | ⚠️ 部分通过 | 能定位 SQLi 证据行（引用了完整 URL）；但枚举前 3 条后即停止，未主动发现 `/upload` + `/uploads/shell.php?cmd=id` 这条已成功的 Web Shell 链。**定向提问**（只问"是否已植入 Web Shell"）则能答对，但依据引用了正常请求 `/static/app.js` 作"文件泄露尝试"，属误判 |

**已知质量现象**（与 `algo=none` 无校准量化相关）：

- 回答中途会从中文切换到英文（如用例 4 定向提问所列 1./2./3. 三条用了英文）
- 生成常在 110–160 token 处提前结束（EOS 提前触发），需要更长输出时应把 `max_new_tokens` 设大并接受截断
- 逐行枚举类任务耐心不足，更适合**定向提问**而非开放式"全面分析"

> 若上述退化影响业务，需在 **≥160GB 内存**机器上用 `--algo normal --opt-level 1` 重转（见转换机 `/home/dev/ljh/bimo/scripts/export_bimo.py`）。

