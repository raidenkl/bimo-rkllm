# 第三方组件与许可说明

本仓库只包含自己写的脚本、校准数据和文档，不包含任何厂商二进制或模型权重。

## 模型

Bimo-7B-CyberAgent 由 Saim Azam 发布，许可 Apache-2.0，底座是 Qwen2.5-Coder-7B-Instruct（同为 Apache-2.0）。权重需要自行从 Hugging Face 获取：

- https://huggingface.co/iBimo-o/Bimo-7B-CyberAgent
- 镜像：https://hf-mirror.com/iBimo-o/Bimo-7B-CyberAgent
- GGUF 版：https://hf-mirror.com/mradermacher/Bimo-7B-CyberAgent-GGUF

仓库里 `data/` 下的校准样本是为量化准备的自建内容，与上述模型仓库无关。

## Rockchip RKLLM SDK

以下文件属于 Rockchip RKLLM SDK，**不在本仓库中**，请从官方渠道获取并遵守其许可条款：

- `rkllm_toolkit-*-linux_x86_64.whl`（转换工具，随 SDK 发布）
- `librkllmrt.so`（板端运行时）
- `llm_demo.cpp` / `rkllm.h`（示例代码与头文件）

获取方式：

```bash
git clone https://github.com/airockchip/rknn-llm.git
# 或从 Rockchip 的 SDK 发布页下载对应版本
```

需要注意版本：本次验证用的是 SDK **1.2.3**（`librkllmrt.so` build 77daf2e2）。板端 runtime 必须与转换工具链同版本，否则 `rkllm_init` 会返回非 0，日志出现 `version check encountered an error`。

## Python 依赖

见 `requirements-lock.txt`。这些包各自的许可请参考其 PyPI 页面。

## 文档中的地址

文档与脚本里的 IP 都做了脱敏处理，或使用 RFC 5737 规定的文档专用地址段（192.0.2.0/24、198.51.100.0/24、203.0.113.0/24）。示例日志中的域名与账号均为虚构。
