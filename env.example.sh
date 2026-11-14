#!/usr/bin/env bash
# 环境变量模板。用法: cp env.example.sh local.env && vim local.env && source local.env
# local.env 已在 .gitignore 里，不会被提交

# 工作目录根。脚本会读这个变量，默认值指向作者机器的布局
export BIMO_ROOT=/data/bimo

# RKLLM SDK 根目录（含 rkllm-toolkit/packages 和 rkllm-runtime）
export SDK=/opt/rknn-llm

# 无 GPU 时保持为空；有 GPU 时填 0
export CUDA_VISIBLE_DEVICES=""

# 线程数，建议等于物理核数
export OMP_NUM_THREADS=64
export MKL_NUM_THREADS=64
export TOKENIZERS_PARALLELISM=false

# 国内走镜像
export HF_ENDPOINT=https://hf-mirror.com
